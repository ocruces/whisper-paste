"""Tests for the manifest-backed runtime model cache."""

import hashlib
from pathlib import Path

import pytest

from whisper_paste import model_cache


REVISION = "a" * 40


def _manifest(*, name="small", revision=REVISION, hashes=None):
    return {
        name: {
            "repo_id": "example/whisper-small",
            "revision": revision,
            "sha256": (
                {
                    "config.json": hashlib.sha256(b"config").hexdigest(),
                    "model.bin": hashlib.sha256(b"weights").hexdigest(),
                }
                if hashes is None
                else hashes
            ),
        }
    }


def _write_payload(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        path = directory / filename
        path.write_bytes(content)


def _patch_manifest(monkeypatch, manifest):
    monkeypatch.setattr(model_cache, "load_manifest", lambda: manifest)


def _patch_cache_root(monkeypatch, tmp_path):
    root = tmp_path / "WhisperPaste" / "models"
    monkeypatch.setattr(model_cache, "cache_root", lambda: root)
    return root


def test_validate_manifest_accepts_existing_schema():
    manifest = _manifest()

    result = model_cache.validate_manifest(manifest)

    assert result == manifest


def test_load_manifest_reads_the_packaged_resource():
    model_cache.load_manifest.cache_clear()

    manifest = model_cache.load_manifest()

    assert manifest["small"]["repo_id"] == "Systran/faster-whisper-small"
    assert manifest["small"]["revision"] == "536b0662742c02347bc0e980a01041f333bce120"
    assert set(manifest["small"]["sha256"]) == {
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt",
    }


@pytest.mark.parametrize(
    "manifest",
    [
        _manifest(name="bad/name"),
        _manifest(revision="not-a-commit"),
        _manifest(hashes={"../model.bin": "0" * 64}),
        _manifest(hashes={"model.bin": "not-a-sha256"}),
        _manifest(hashes={}),
    ],
)
def test_validate_manifest_rejects_malformed_entries(manifest):
    with pytest.raises(ValueError):
        model_cache.validate_manifest(manifest)


def test_cache_root_prefers_localappdata(monkeypatch, tmp_path):
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert model_cache.cache_root() == local_app_data / "WhisperPaste" / "models"


def test_cache_root_falls_back_to_user_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(model_cache.os.path, "expanduser", lambda value: str(home))

    assert model_cache.cache_root() == home / "WhisperPaste" / "models"


def test_cache_hit_checks_required_files_without_rehashing(monkeypatch, tmp_path):
    manifest = _manifest()
    root = _patch_cache_root(monkeypatch, tmp_path)
    _patch_manifest(monkeypatch, manifest)
    entry_dir = root / "small" / REVISION
    _write_payload(entry_dir, {"config.json": b"config", "model.bin": b"weights"})
    monkeypatch.setattr(
        model_cache,
        "_sha256_file",
        lambda path: pytest.fail(f"cache hit unexpectedly hashed {path}"),
    )

    result = model_cache.ensure_model("small")

    assert result == entry_dir


def test_unknown_model_is_rejected_without_a_download(monkeypatch, tmp_path):
    _patch_cache_root(monkeypatch, tmp_path)
    _patch_manifest(monkeypatch, _manifest())
    monkeypatch.setattr(
        model_cache,
        "_snapshot_download",
        lambda **kwargs: pytest.fail("unknown model must not download"),
    )

    with pytest.raises(model_cache.ModelDownloadError, match="trusted manifest"):
        model_cache.ensure_model("Systran/faster-whisper-medium")


def test_download_uses_pinned_revision_exact_patterns_and_publishes_atomically(
    monkeypatch, tmp_path
):
    manifest = _manifest()
    root = _patch_cache_root(monkeypatch, tmp_path)
    _patch_manifest(monkeypatch, manifest)
    calls = []
    replacements = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        _write_payload(
            Path(kwargs["local_dir"]),
            {"config.json": b"config", "model.bin": b"weights"},
        )
        (Path(kwargs["local_dir"]) / ".cache" / "huggingface").mkdir(
            parents=True
        )

    real_replace = model_cache.os.replace

    def record_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        assert (Path(source) / "config.json").is_file()
        assert (Path(source) / "model.bin").is_file()
        return real_replace(source, destination)

    monkeypatch.setattr(model_cache, "_snapshot_download", fake_download)
    monkeypatch.setattr(model_cache.os, "replace", record_replace)

    result = model_cache.ensure_model("small")

    assert result == root / "small" / REVISION
    assert calls == [
        {
            "repo_id": "example/whisper-small",
            "revision": REVISION,
            "local_dir": str(replacements[0][0]),
            "allow_patterns": ["config.json", "model.bin"],
        }
    ]
    assert replacements[0][1] == result
    assert not (result / ".cache").exists()


def test_checksum_failure_cleans_staging_and_does_not_publish(monkeypatch, tmp_path):
    manifest = _manifest()
    root = _patch_cache_root(monkeypatch, tmp_path)
    _patch_manifest(monkeypatch, manifest)

    def fake_download(**kwargs):
        _write_payload(
            Path(kwargs["local_dir"]),
            {"config.json": b"config", "model.bin": b"tampered"},
        )

    monkeypatch.setattr(model_cache, "_snapshot_download", fake_download)

    with pytest.raises(model_cache.ModelDownloadError, match="checksum") as caught:
        model_cache.ensure_model("small")

    assert isinstance(caught.value.__cause__, ValueError)
    assert not (root / "small" / REVISION).exists()
    assert list((root / "small").glob("*.staging-*")) == []


def test_missing_or_unexpected_payload_is_rejected_and_cleaned(monkeypatch, tmp_path):
    manifest = _manifest()
    root = _patch_cache_root(monkeypatch, tmp_path)
    _patch_manifest(monkeypatch, manifest)

    def fake_download(**kwargs):
        _write_payload(Path(kwargs["local_dir"]), {"config.json": b"config"})
        (Path(kwargs["local_dir"]) / "unexpected.bin").write_bytes(b"extra")

    monkeypatch.setattr(model_cache, "_snapshot_download", fake_download)

    with pytest.raises(model_cache.ModelDownloadError, match="unexpected"):
        model_cache.ensure_model("small")

    assert not (root / "small" / REVISION).exists()
    assert list((root / "small").glob("*.staging-*")) == []


def test_download_exception_is_concise_and_preserves_cause(monkeypatch, tmp_path):
    manifest = _manifest()
    root = _patch_cache_root(monkeypatch, tmp_path)
    _patch_manifest(monkeypatch, manifest)
    original = RuntimeError("network failure with lots of implementation detail")

    def fake_download(**kwargs):
        raise original

    monkeypatch.setattr(model_cache, "_snapshot_download", fake_download)

    with pytest.raises(model_cache.ModelDownloadError, match="small") as caught:
        model_cache.ensure_model("small")

    assert caught.value.__cause__ is original
    assert len(str(caught.value)) < 160
    assert not (root / "small" / REVISION).exists()
    assert list((root / "small").glob("*.staging-*")) == []


def test_stale_staging_directories_are_removed(monkeypatch, tmp_path):
    manifest = _manifest()
    root = _patch_cache_root(monkeypatch, tmp_path)
    _patch_manifest(monkeypatch, manifest)
    model_root = root / "small"
    stale = model_root / f".small.{REVISION}.staging-old"
    stale.mkdir(parents=True)
    (stale / "partial").write_bytes(b"partial")

    def fake_download(**kwargs):
        _write_payload(
            Path(kwargs["local_dir"]),
            {"config.json": b"config", "model.bin": b"weights"},
        )

    monkeypatch.setattr(model_cache, "_snapshot_download", fake_download)

    model_cache.ensure_model("small")

    assert not stale.exists()
