"""Tests for the portable-ZIP build *inputs*.

These guard reproducibility, not runtime behaviour. Nothing here builds a
bundle, downloads a model or touches the network — it is pure file inspection,
so it runs in the normal suite in milliseconds and fails on a bad build input
long before someone waits 85 seconds for PyInstaller or 461 MB for a model.

What is deliberately NOT tested here: whether the frozen exe works. That cannot
be asserted from a source checkout; it is verified by actually building and
running the bundle (see the build/verify steps in the repo docs).
"""

import configparser
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
import types
import zipfile

import pytest
from PIL import Image

import whisper_paste

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGING = os.path.join(REPO_ROOT, "packaging")

REQUIREMENTS_BUILD = os.path.join(REPO_ROOT, "requirements-build.txt")
PYPROJECT = os.path.join(REPO_ROOT, "pyproject.toml")
SPEC = os.path.join(PACKAGING, "whisper-paste.spec")
MODELS_JSON = os.path.join(REPO_ROOT, "whisper_paste", "resources", "models.json")
LEGACY_MODELS_JSON = os.path.join(PACKAGING, "models.json")
FETCH_MODEL = os.path.join(PACKAGING, "fetch_model.py")
VERSION_INFO = os.path.join(PACKAGING, "version_info.txt")
ICON = os.path.join(PACKAGING, "whisper-paste.ico")
LAUNCHER_TEMPLATE = os.path.join(PACKAGING, "launcher-template.cmd")
SETTINGS_TEMPLATE = os.path.join(PACKAGING, "whisper-paste.ini")
BUILD_PS1 = os.path.join(REPO_ROOT, "scripts", "build.ps1")
INSTALL_PS1 = os.path.join(REPO_ROOT, "scripts", "install.ps1")
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "tests.yml")
LEGACY_REQUIREMENTS = os.path.join(REPO_ROOT, "requirements.txt")
LEGACY_REQUIREMENTS_DEV = os.path.join(REPO_ROOT, "requirements-dev.txt")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _requirement_lines():
    """The actual pins, one logical requirement per entry.

    Comments and blank lines are stripped, and a trailing `\\` continuation —
    used to carry a `--hash=sha256:...` line onto the `name==version` above it
    — is joined back onto the requirement it belongs to, the same way pip
    itself parses the file. Without the join, every hashed entry becomes two
    short lines instead of one, and `test_every_build_requirement_is_pinned_
    exactly` would flag the `--hash=...` line as an unpinned requirement.
    """
    logical = []
    buffer = None
    for raw_line in _read(REQUIREMENTS_BUILD).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if buffer is not None:
            line = buffer + " " + line
            buffer = None
        if line.endswith("\\"):
            buffer = line[:-1].rstrip()
            continue
        logical.append(line)
    return logical


# --------------------------------------------------------------------------
# requirements-build.txt
# --------------------------------------------------------------------------


def test_every_build_requirement_is_pinned_exactly():
    """`>=` or a bare name would let the shipped ZIP drift between builds.

    The whole point of a separate requirements-build.txt is that it is the
    exact transitive closure that was measured to freeze correctly. One
    unpinned line and the closure is no longer a closure.
    """
    unpinned = [line for line in _requirement_lines()
                if not re.match(r"^[A-Za-z0-9._-]+==", line)]

    assert unpinned == [], f"not pinned with '==': {unpinned}"


def test_every_build_requirement_carries_a_hash():
    """`==` pins a version string, not the bytes pip actually installs.

    BUILD-INFO.txt records the SHA-256 of this file's *text*, not of the
    resolved wheels, so a wheel swapped out from under an unchanged version
    pin would produce an identical BUILD-INFO line — the file would look
    untampered while shipping different content. A `--hash=sha256:` per
    requirement is what lets `pip install --require-hashes` (see
    scripts\\build.ps1) refuse to install anything but the exact wheel bytes
    that were measured when this file was generated.
    """
    hash_re = re.compile(r"--hash=sha256:([0-9a-f]{64})\b")

    for line in _requirement_lines():
        assert hash_re.search(line), f"no --hash=sha256:<64 hex chars> on: {line}"


def test_build_requirements_include_setuptools_for_the_environment_audit():
    """A Python 3.11 venv seeds setuptools, and audit.ps1 scans the whole venv."""
    assert any(line.startswith("setuptools==") for line in _requirement_lines())


def test_source_setup_upgrades_both_audited_packaging_tools():
    """Fresh Python 3.11 venvs seed vulnerable setuptools and an old pip."""
    commands = [
        line for line in _read(INSTALL_PS1).splitlines()
        if "-m pip install --upgrade" in line and " -r " not in line
        and not line.lstrip().startswith("#")
    ]

    assert len(commands) == 1
    assert re.search(r"--upgrade\s+pip\s+setuptools(?:\s|$)", commands[0])


def test_build_requirements_are_not_empty():
    """Guards the parser above: an empty file would pass every other check."""
    assert len(_requirement_lines()) > 10


def test_pywhispercpp_is_absent_from_build_requirements():
    """--gpu is a source-install-only feature; see the header of that file.

    The PyPI pywhispercpp wheel is CPU-only, and the Vulkan build cannot be
    shipped prebuilt, so a pywhispercpp in the bundle could never be reached —
    the portable build rejects --gpu outright.

    Checked against the pins only, not the raw text: the file's header comment
    explains *why* pywhispercpp is absent, so a substring search over the whole
    file matches that explanation and fails against a perfectly correct file.
    """
    offenders = [line for line in _requirement_lines()
                 if line.lower().startswith("pywhispercpp")]

    assert offenders == [], f"pywhispercpp must not be a build input: {offenders}"


def test_build_python_version_is_greppable():
    """scripts\\build.ps1 greps this line rather than hard-coding 3.11 twice.

    PyInstaller bundles the interpreter it runs under, so the build script and
    this file disagreeing means users silently get a different Python.
    """
    assert re.search(r"^#\s*BUILD_PYTHON\s*=\s*3\.\d+\s*$",
                     _read(REQUIREMENTS_BUILD), re.MULTILINE)


# --------------------------------------------------------------------------
# whisper-paste.spec
# --------------------------------------------------------------------------


def _spec_excludes():
    """The quoted entries of the spec's `excludes = [...]` list.

    Parsed rather than exec'd: importing the spec would need PyInstaller
    installed in the dev venv, which it is not (it lives only in the build
    venv). Comments are stripped so a module merely *mentioned* in a comment
    does not count as excluded.
    """
    text = _read(SPEC)
    block = re.search(r"^excludes = \[(.*?)^\]", text, re.MULTILINE | re.DOTALL)
    assert block, "could not find the `excludes = [...]` list in the spec"
    body = re.sub(r"#.*", "", block.group(1))
    return re.findall(r"""["']([^"']+)["']""", body)


def test_spec_excludes_pywhispercpp():
    """Second, independent guard — the pin above is the first.

    Neither is redundant: the pin has no say if someone builds in an activated
    .venv instead of the clean build venv, and the exclude has no say if the
    spec is edited. Both have to keep the whisper.cpp runtime out.
    """
    assert "pywhispercpp" in _spec_excludes()


def test_spec_does_not_exclude_hf_xet_or_av():
    """Two modules that look like easy size wins and are not.

    hf_xet: huggingface_hub uses it for Xet-backed repos, which is how the
    faster-whisper model repos are served — excluding it risks breaking the
    `--model <not-bundled>` download fallback. av: faster_whisper/__init__.py
    imports audio.py, which does a top-level `import av`, so excluding it
    breaks every faster_whisper import. Both measured, not assumed.
    """
    excludes = _spec_excludes()

    assert "hf_xet" not in excludes
    assert "av" not in excludes


def test_spec_collects_faster_whisper_data_files():
    """The one mandatory directive.

    faster_whisper computes the Silero VAD asset path from its own __file__,
    which static analysis cannot see. Without this the frozen build has no
    silero_vad_v6.onnx and every transcription fails, because transcriber.py
    passes vad_filter=True on the faster-whisper path.
    """
    assert re.search(r"""collect_data_files\(\s*["']faster_whisper["']\s*\)""",
                     _read(SPEC))


def test_spec_builds_both_the_windowed_exe_and_the_console_twin():
    """A GUI-subsystem exe does not attach to the console that launched it.

    Measured: sys.stdout is None and GetConsoleWindow() == 0. So no wrapper
    script can make WhisperPaste.exe print anything, and the console twin is
    the only way a user can read a crash. Losing it would be a silent
    regression — the ZIP would still build and still work until something
    went wrong.
    """
    text = _read(SPEC)

    assert re.search(r"""name=["']WhisperPaste["']""", text)
    assert re.search(r"""name=["']WhisperPaste-debug["']""", text)
    assert "console=False" in text
    assert "console=True" in text


# --------------------------------------------------------------------------
# canonical manifest and package data
# --------------------------------------------------------------------------


def _models():
    import json

    with open(MODELS_JSON, encoding="utf-8") as fh:
        manifest = json.load(fh)
    # `_comment` and friends are documentation, not entries.
    return {k: v for k, v in manifest.items() if not k.startswith("_")}


def test_models_json_lists_the_default_model():
    """`small` is what build.ps1 ships by default; losing it breaks the build."""
    assert "small" in _models()


EXPECTED_FASTER_WHISPER_MODELS = {
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "tiny": "Systran/faster-whisper-tiny",
    "base.en": "Systran/faster-whisper-base.en",
    "base": "Systran/faster-whisper-base",
    "small.en": "Systran/faster-whisper-small.en",
    "small": "Systran/faster-whisper-small",
    "medium.en": "Systran/faster-whisper-medium.en",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "distil-large-v3.5": "distil-whisper/distil-large-v3.5-ct2",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}


def test_manifest_covers_the_supported_faster_whisper_model_names():
    """Every accepted faster-whisper name must resolve to a reviewed entry."""
    models = _models()

    assert {name: entry["repo_id"] for name, entry in models.items()} == \
        EXPECTED_FASTER_WHISPER_MODELS


def test_manifest_preserves_the_approved_small_entry_exactly():
    assert _models()["small"] == {
        "repo_id": "Systran/faster-whisper-small",
        "revision": "536b0662742c02347bc0e980a01041f333bce120",
        "sha256": {
            "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
            "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
            "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
            "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
        },
    }


def test_canonical_manifest_replaces_the_legacy_manifest():
    assert os.path.isfile(MODELS_JSON)
    assert not os.path.exists(LEGACY_MODELS_JSON)


def test_project_declares_manifest_package_data_and_dev_extra():
    with open(PYPROJECT, "rb") as fh:
        pyproject = tomllib.load(fh)

    assert pyproject["project"]["optional-dependencies"]["dev"] == ["pytest"]
    assert "resources/models.json" in pyproject["tool"]["setuptools"][
        "package-data"
    ]["whisper_paste"]


def test_wheel_contains_the_canonical_manifest(tmp_path):
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            REPO_ROOT,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        assert "whisper_paste/resources/models.json" in wheel.namelist()


def test_legacy_requirement_files_are_removed():
    assert not os.path.exists(LEGACY_REQUIREMENTS)
    assert not os.path.exists(LEGACY_REQUIREMENTS_DEV)


def test_install_and_ci_use_the_dev_extra():
    install = _read(INSTALL_PS1)
    workflow = _read(WORKFLOW)

    assert "installTarget = if ($Dev)" in install
    assert re.search(r"-m pip install -e \$installTarget", install)
    assert ".[dev]" in install
    assert re.search(r"pip install -e ['\"]\.\[dev\]['\"]", workflow)
    assert "requirements.txt" not in install
    assert "requirements-dev.txt" not in install
    assert "requirements-dev.txt" not in workflow


def test_allowed_packaging_inputs_have_no_legacy_manifest_or_requirement_refs():
    paths = [
        os.path.join(REPO_ROOT, "CLAUDE.md"),
        os.path.join(REPO_ROOT, "README.md"),
        os.path.join(REPO_ROOT, "docs", "security-review-portable-zip.md"),
        REQUIREMENTS_BUILD,
        INSTALL_PS1,
        BUILD_PS1,
        FETCH_MODEL,
        SPEC,
        WORKFLOW,
    ]
    legacy = re.compile(r"packaging[\\/]models\.json|requirements(?:-dev)?\.txt")

    offenders = {
        path: legacy.findall(_read(path))
        for path in paths
        if legacy.search(_read(path))
    }
    assert offenders == {}


def test_build_fetch_and_spec_use_the_canonical_manifest():
    build = _read(BUILD_PS1)
    fetch = _read(FETCH_MODEL)
    spec = _read(SPEC)

    assert "whisper_paste\\resources\\models.json" in build
    assert "packaging\\models.json" not in build
    assert "packaging\\models.json" not in fetch
    assert "whisper_paste" in spec and "resources" in spec and "models.json" in spec
    assert "_internal\\whisper_paste\\resources\\models.json" in build


def test_build_rejects_extra_payload_and_avoids_huggingface_cache_metadata():
    build = _read(BUILD_PS1)

    assert ".cache" in build
    assert "unexpected" in build.lower()
    assert "Copy-Item -LiteralPath" in build


def test_fetch_derives_exact_allow_patterns_and_rejects_extra_files(tmp_path, monkeypatch):
    module_path = os.path.join(str(tmp_path), "fetch_model_under_test.py")
    spec = importlib.util.spec_from_file_location("fetch_model_under_test", FETCH_MODEL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    manifest_path = tmp_path / "models.json"
    manifest_path.write_text(
        json.dumps(
            {
                "small": {
                    "repo_id": "example/model",
                    "revision": "a" * 40,
                    "sha256": {
                        "config.json": "0" * 64,
                        "model.bin": "1" * 64,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    dest = tmp_path / "model"
    calls = {}

    def snapshot_download(repo_id, revision, local_dir, allow_patterns):
        calls.update(
            repo_id=repo_id,
            revision=revision,
            allow_patterns=allow_patterns,
        )
        os.makedirs(local_dir, exist_ok=True)
        for filename in ("config.json", "model.bin", "unexpected.txt"):
            with open(os.path.join(local_dir, filename), "wb") as fh:
                fh.write(b"payload")
        os.makedirs(os.path.join(local_dir, ".cache", "huggingface"))
        with open(os.path.join(local_dir, ".cache", "huggingface", "metadata"), "w") as fh:
            fh.write("metadata")

    fake_hub = types.ModuleType("huggingface_hub")
    fake_hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)

    result = module.main([str(manifest_path), "small", str(dest)])

    assert result == 1
    assert calls["repo_id"] == "example/model"
    assert calls["revision"] == "a" * 40
    assert calls["allow_patterns"] == ["config.json", "model.bin"]
    assert not (dest / ".cache").exists()


@pytest.mark.parametrize("name", sorted(_models()))
def test_model_entry_is_pinned_and_hashed(name):
    """Catches a typo'd revision or hash *before* a 461 MB download, not after.

    A branch name in `revision` would silently defeat pinning — the bundled
    weights could change between two builds of the same commit — so only a full
    40-character commit SHA is accepted, matching the check in fetch_model.py.
    """
    entry = _models()[name]

    assert isinstance(entry.get("repo_id"), str) and "/" in entry["repo_id"]
    assert re.fullmatch(r"[0-9a-f]{40}", entry.get("revision", "")), \
        f"{name}: revision must be a 40-char commit SHA, never a branch"

    hashes = entry.get("sha256")
    assert isinstance(hashes, dict) and hashes, f"{name}: no sha256 block"
    for filename, digest in hashes.items():
        assert re.fullmatch(r"[0-9a-f]{64}", digest), \
            f"{name}/{filename}: not a sha256 digest"


# --------------------------------------------------------------------------
# version resource
# --------------------------------------------------------------------------


def test_version_resource_matches_the_package_version():
    """Three places state the version; a mismatch ships a mislabelled exe.

    The Windows VERSIONINFO resource is 4-part (a.b.c.d) while the package
    version is 3-part, so the comparison pads with a trailing 0 rather than
    demanding the strings be equal.
    """
    text = _read(VERSION_INFO)
    expected = whisper_paste.__version__
    expected_4 = expected + ".0"
    expected_tuple = tuple(int(p) for p in expected_4.split("."))

    for field in ("FileVersion", "ProductVersion"):
        found = re.search(
            rf"StringStruct\(u'{field}', u'([^']+)'\)", text
        )
        assert found, f"{field} not found in version_info.txt"
        assert found.group(1) == expected_4, \
            f"{field} is {found.group(1)}, expected {expected_4}"

    for field in ("filevers", "prodvers"):
        found = re.search(rf"{field}=\(([^)]*)\)", text)
        assert found, f"{field} not found in version_info.txt"
        parts = tuple(int(p.strip()) for p in found.group(1).split(","))
        assert parts == expected_tuple, \
            f"{field} is {parts}, expected {expected_tuple}"

    with open(PYPROJECT, "rb") as fh:
        pyproject = tomllib.load(fh)
    assert pyproject["project"]["version"] == expected


# --------------------------------------------------------------------------
# the exe icon
# --------------------------------------------------------------------------

EXPECTED_ICON_SIZES = [
    (16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)
]


def test_icon_ships_every_expected_size():
    """Windows picks a frame per context: 16 in the taskbar, 256 in Explorer.

    Dropping one makes Windows downscale, which looks muddy at small sizes.
    """
    with Image.open(ICON) as img:
        assert sorted(img.ico.sizes()) == EXPECTED_ICON_SIZES


def test_icon_256_frame_matches_the_tray_artwork():
    """The exe icon and the tray icon must be one drawing, not two.

    make_icon.py imports create_icon_image rather than reimplementing it; this
    test is what fails if the committed .ico is not regenerated after the
    artwork changes. Fix by re-running:

        .venv\\Scripts\\python.exe packaging\\make_icon.py

    Only the 256 frame is compared, and that is a measurement, not caution:
    Pillow's ICO encoder writes the smaller frames as LANCZOS downsamples of
    the 256 render, so the stored 64x64 frame differs from a direct
    create_icon_image(..., size=64) render in 16.3% of its bytes. Asserting on
    64 would fail against a perfectly correct icon. Do not "fix" this test to
    use a smaller frame.

    The whole file is deliberately not byte-compared either: Pillow's ICO
    encoder output is not stable across Pillow versions, so that would turn a
    routine dependency bump into a spurious failure.
    """
    from whisper_paste.app import COLOR_IDLE, create_icon_image

    reference = create_icon_image(COLOR_IDLE, size=256)

    with Image.open(ICON) as img:
        # Converted inside the `with`: getimage() returns a lazily-decoded
        # image, so touching its pixels after Image.open's context manager has
        # closed the underlying file raises "seek of closed file".
        stored = img.ico.getimage((256, 256))
        stored_size = stored.size
        stored_bytes = stored.convert("RGBA").tobytes()

    assert stored_size == (256, 256)
    assert stored_bytes == reference.tobytes()


# --------------------------------------------------------------------------
# launcher-template.cmd
# --------------------------------------------------------------------------

PLACEHOLDER = "@@LANG@@"


def _launcher_commands():
    """The template's executable lines - comments and blanks removed.

    Everything else in the file is REM prose, which legitimately contains
    example command lines (including other languages); only these lines end up
    doing anything.
    """
    return [
        line.strip()
        for line in _read(LAUNCHER_TEMPLATE).splitlines()
        if line.strip() and not re.match(r"^\s*@?rem\b", line, re.IGNORECASE)
    ]


def test_launcher_template_keeps_the_load_bearing_idioms():
    """build.ps1 expands this once per -Languages code; three bits must survive.

    `""` is start's window title: without it start swallows the quoted exe path
    as the title and has nothing left to run. `%~dp0` is the script's own
    directory, so the launcher works wherever the ZIP was unpacked and whatever
    the CWD is. `%*` forwards extra flags, which is also how a user overrides
    the baked-in language for one run (argparse takes the last occurrence).
    """
    starts = [line for line in _launcher_commands() if "start" in line]

    assert len(starts) == 1, f"expected exactly one start line, got {starts}"
    line = starts[0]
    assert line.startswith("@"), "the command line must be @-prefixed (no echo)"
    assert 'start ""' in line, 'the empty start title `""` is required'
    assert "%~dp0WhisperPaste.exe" in line
    assert f"--lang {PLACEHOLDER}" in line
    assert line.rstrip().endswith("%*"), "extra flags must be forwarded"


def test_launcher_template_hard_codes_no_language():
    """A literal code here would ship every launcher dictating that language.

    Only the executable lines are inspected: the REM block deliberately shows
    `--lang fr` as an example of overriding the language on the command line.
    """
    for line in _launcher_commands():
        for code in re.findall(r"--lang\s+(\S+)", line):
            assert code == PLACEHOLDER, f"hard-coded language in: {line}"


def test_launcher_template_documents_that_it_is_generated():
    """The copy in the ZIP is overwritten by the next build - say so there.

    And the single-instance mutex: launching a second copy to "switch language"
    shows an already-running message instead, which looks like a bug unless the
    file says otherwise.
    """
    text = _read(LAUNCHER_TEMPLATE).lower()

    assert "scripts\\build.ps1" in text
    assert "launcher-template.cmd" in text
    assert "quit" in text and "already running" in text


def test_launcher_template_is_pure_ascii():
    """cmd.exe reads batch files in the OEM codepage, not UTF-8.

    A smart quote or accented character in a REM line would render as mojibake;
    in a command line it would break the command. Staying ASCII sidesteps the
    whole codepage question - and build.ps1 writes the expansion with
    -Encoding ascii on that assumption.
    """
    raw = open(LAUNCHER_TEMPLATE, "rb").read()

    assert not raw.startswith(b"\xef\xbb\xbf"), "no UTF-8 BOM: cmd.exe chokes on it"
    raw.decode("ascii")  # raises UnicodeDecodeError if not


# --------------------------------------------------------------------------
# whisper-paste.ini (the shipped settings template)
# --------------------------------------------------------------------------

# The reader's contract. Documenting a key here that the reader does not
# implement (or vice versa) is a silent no-op for the user, so the two lists
# are pinned to each other through this one.
SETTINGS_SECTION = "whisper-paste"
SETTINGS_KEYS = {
    "language", "model", "hotkey", "refine", "output", "log_transcripts",
    "log_dir",
}


def _commented_settings_keys():
    """Keys documented as commented-out `;key = value` lines.

    Prose comments never match: the key has to sit immediately after the
    comment marker and be followed by `=`.
    """
    return [
        m.group(1)
        for m in re.finditer(
            r"^[ \t]*[;#][ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=",
            _read(SETTINGS_TEMPLATE), re.MULTILINE,
        )
    ]


def test_settings_template_parses_and_has_only_the_one_section():
    """A stray section header would make every key under it unreachable."""
    parser = configparser.ConfigParser()
    parser.read(SETTINGS_TEMPLATE, encoding="utf-8")

    assert parser.sections() == [SETTINGS_SECTION]


def test_settings_template_is_inert_as_shipped():
    """Shipping it must change nothing until the user uncomments a line.

    Every key is commented out, so the parsed section is empty; anything live
    in here would silently override the defaults for every user of the ZIP.
    """
    parser = configparser.ConfigParser()
    parser.read(SETTINGS_TEMPLATE, encoding="utf-8")

    assert parser.options(SETTINGS_SECTION) == []
    assert parser.defaults() == {}


def test_settings_template_documents_exactly_the_contract_keys():
    """Guards against the template and the settings reader drifting apart.

    A key documented here but not read is a setting that silently does
    nothing; a key read but not documented is one nobody discovers.
    """
    documented = _commented_settings_keys()

    assert sorted(documented) == sorted(set(documented)), \
        f"a key is documented twice: {documented}"
    assert set(documented) == SETTINGS_KEYS


def test_settings_template_documents_the_output_and_boolean_vocabularies():
    """The values are as much a contract as the key names."""
    text = _read(SETTINGS_TEMPLATE)

    assert "clipboard" in text and "type" in text
    assert re.search(r"true or false", text, re.IGNORECASE)


def test_settings_template_explains_precedence_and_the_other_locations():
    """Three lookup places and one precedence rule the user cannot guess."""
    text = _read(SETTINGS_TEMPLATE)

    assert "--config" in text
    assert "%LOCALAPPDATA%\\WhisperPaste\\whisper-paste.ini" in text
    assert re.search(r"command[- ]line.*(override|win)", text, re.IGNORECASE)


def test_settings_template_is_editable_in_notepad():
    """Non-technical users open this in Notepad; keep it plain.

    ASCII with no BOM, so it survives a Notepad round-trip (which may save as
    ANSI) without turning comments into mojibake.
    """
    raw = open(SETTINGS_TEMPLATE, "rb").read()

    assert not raw.startswith(b"\xef\xbb\xbf")
    raw.decode("ascii")


# --------------------------------------------------------------------------
# build.ps1 wiring
# --------------------------------------------------------------------------


def test_build_script_takes_a_languages_parameter_defaulting_to_en_es():
    """en,es is what the ZIP ships; changing it is a release decision."""
    text = _read(BUILD_PS1)

    assert re.search(r"\[string\]\$Languages\s*=\s*'en,es'", text)
    assert ".PARAMETER Languages" in text


def test_build_script_stages_the_launchers_and_the_settings_file():
    """Both must land in the ZIP root, next to WhisperPaste.exe."""
    text = _read(BUILD_PS1)

    assert "launcher-template.cmd" in text
    assert "whisper-paste.ini" in text
    assert '"WhisperPaste-$code.cmd"' in text


def test_build_script_requires_hashes():
    """Keep hash checking explicit rather than incidental.

    pip enables hash-checking mode on its own as soon as any requirement
    carries a `--hash` ("This option is implied when any package in a
    requirements file has a --hash option"), so the flag is not what makes the
    current file verified. What it buys is a floor: if a future regeneration
    ever drops the hashes, implied mode switches itself off silently and the
    build keeps passing with version-only pins, whereas the explicit flag turns
    that same edit into a loud failure. It also means nobody has to know about
    the implication rule to read the build script correctly.

    Paired with test_every_build_requirement_carries_a_hash: that one pins the
    data, this one pins the enforcement.
    """
    text = _read(BUILD_PS1)

    assert "--require-hashes" in text


def test_build_script_writes_launchers_without_a_bom():
    """PowerShell 5.1 defaults to UTF-8-with-BOM, and cmd.exe chokes on it.

    The BOM is parsed as part of the first command, so the generated launcher
    fails with a "'ï»¿@echo' is not recognized" style error - on the user's
    machine, never on the builder's.
    """
    writes = [line for line in _read(BUILD_PS1).splitlines()
              if "Set-Content" in line and "$stageDir $name" in line]

    assert len(writes) == 1, f"expected one launcher write, got {writes}"
    assert "-Encoding ascii" in writes[0]
