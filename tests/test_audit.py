import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "audit.ps1"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


@pytest.fixture(scope="session")
def venv_template(tmp_path_factory):
    path = tmp_path_factory.mktemp("audit-venv-template") / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def _venv(path, template):
    shutil.copytree(template, path)


def _ready_auditor(root, template, exit_code=0):
    audit_venv = root / "build" / "audit-venv"
    _venv(audit_venv, template)
    (audit_venv / ".pip-audit-version").write_text("2.10.1", encoding="ascii")
    package = audit_venv / "Lib" / "site-packages" / "pip_audit"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, sys\n"
        "with open(os.environ['AUDIT_TEST_LOG'], 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'executable': sys.executable, 'prefix': sys.prefix, "
        "'args': sys.argv[1:]}) + '\\n')\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )


def _repo(tmp_path):
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(AUDIT_SCRIPT, scripts / "audit.ps1")
    log = root / "audit.jsonl"
    env = os.environ.copy()
    env["AUDIT_TEST_LOG"] = str(log)
    return root, log, env


def _run(script, *args, cwd=None, env=None):
    assert POWERSHELL, "PowerShell is required for the Windows build scripts"
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-File", str(script), *map(str, args)],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )


def _events(log):
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def test_cached_pinned_auditor_checks_default_venv(
        tmp_path, venv_template):
    root, log, env = _repo(tmp_path)
    _venv(root / ".venv", venv_template)
    _ready_auditor(root, venv_template)

    result = _run(root / "scripts" / "audit.ps1", cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    events = _events(log)
    assert len(events) == 1
    assert _norm(events[0]["prefix"]) == _norm(root / "build" / "audit-venv")
    assert events[0]["args"] == [
        "--strict", "--progress-spinner", "off", "--path",
        str(root / ".venv" / "Lib" / "site-packages"),
    ]


def test_accepts_explicit_target_venv(tmp_path, venv_template):
    root, log, env = _repo(tmp_path)
    target = root / "build" / "venv"
    _venv(target, venv_template)
    _ready_auditor(root, venv_template)

    result = _run(
        root / "scripts" / "audit.ps1", "-Venv", target,
        cwd=tmp_path, env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    audit = _events(log)[0]
    assert _norm(audit["args"][-1]) == _norm(target / "Lib" / "site-packages")


@pytest.mark.parametrize("exit_code", [1, 23], ids=["vulnerabilities", "tool_failure"])
def test_audit_failure_is_nonzero(tmp_path, exit_code, venv_template):
    root, _, env = _repo(tmp_path)
    _venv(root / ".venv", venv_template)
    _ready_auditor(root, venv_template, exit_code)

    result = _run(root / "scripts" / "audit.ps1", cwd=tmp_path, env=env)

    assert result.returncode != 0


def test_broken_cached_auditor_is_nonzero(tmp_path, venv_template):
    root, log, env = _repo(tmp_path)
    _venv(root / ".venv", venv_template)
    audit_venv = root / "build" / "audit-venv"
    (audit_venv / "Scripts" / "python.exe").mkdir(parents=True)
    (audit_venv / ".pip-audit-version").write_text("2.10.1", encoding="ascii")

    result = _run(root / "scripts" / "audit.ps1", cwd=tmp_path, env=env)

    assert result.returncode != 0
    assert _events(log) == []


def test_ignores_pythonpath_scanner_shadow(tmp_path, venv_template):
    root, log, env = _repo(tmp_path)
    _venv(root / ".venv", venv_template)
    _ready_auditor(root, venv_template, exit_code=23)
    shadow = root / "shadow" / "pip_audit"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "__main__.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    env["PYTHONPATH"] = str(shadow.parent)

    result = _run(root / "scripts" / "audit.ps1", cwd=tmp_path, env=env)

    assert result.returncode != 0
    assert len(_events(log)) == 1


def test_missing_target_venv_is_nonzero(tmp_path):
    root, log, env = _repo(tmp_path)

    result = _run(root / "scripts" / "audit.ps1", cwd=tmp_path, env=env)

    assert result.returncode != 0
    assert _events(log) == []


def test_build_stops_when_dependency_audit_fails(tmp_path, venv_template):
    root = tmp_path / "repo"
    scripts = root / "scripts"
    packaging = root / "packaging"
    resources = root / "whisper_paste" / "resources"
    scripts.mkdir(parents=True)
    packaging.mkdir()
    resources.mkdir(parents=True)
    shutil.copy2(BUILD_SCRIPT, scripts / "build.ps1")
    probe = root / "audit-probe.txt"
    (scripts / "audit.ps1").write_text(
        "param([string]$Venv)\n"
        "[IO.File]::WriteAllText($env:AUDIT_PROBE_LOG, $Venv)\n"
        "exit 23\n",
        encoding="utf-8",
    )
    for name in ("whisper-paste.spec", "fetch_model.py", "launcher-template.cmd",
                 "whisper-paste.ini"):
        (packaging / name).write_text("", encoding="utf-8")
    (resources / "models.json").write_text(
        json.dumps({"small": {"repo_id": "example/model", "revision": "a" * 40,
                              "sha256": {}}}),
        encoding="utf-8",
    )
    requirements = root / "requirements-build.txt"
    requirements.write_text("# BUILD_PYTHON = 0.0\n", encoding="utf-8")
    build_venv = root / "build" / "venv"
    _venv(build_venv, venv_template)
    (build_venv / ".reqs.sha256").write_text(
        hashlib.sha256(requirements.read_bytes()).hexdigest(), encoding="utf-8"
    )
    (root / "build" / "models" / "small").mkdir(parents=True)
    (root / "whisper_paste" / "__init__.py").write_text(
        '__version__ = "1.0.0"\n', encoding="utf-8"
    )
    env = os.environ.copy()
    env["AUDIT_PROBE_LOG"] = str(probe)

    result = _run(
        scripts / "build.ps1", "-AllowPythonMismatch", "-SkipZip",
        cwd=tmp_path, env=env,
    )

    assert result.returncode != 0
    assert probe.exists(), result.stdout + result.stderr
    assert _norm(probe.read_text(encoding="utf-8")) == _norm(build_venv)
    assert "Running PyInstaller" not in result.stdout
