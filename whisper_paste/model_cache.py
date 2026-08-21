"""Manifest-backed downloads and cache management for faster-whisper models.

Only model entries reviewed into the packaged manifest may reach
``huggingface_hub.snapshot_download``.  Downloads are verified in a private
staging directory before a complete model directory is published.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

from importlib import resources


_MANIFEST_PACKAGE = "whisper_paste"
_MANIFEST_RESOURCE = ("resources", "models.json")
_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_ENTRY_FIELDS = {"repo_id", "revision", "sha256"}


class ManifestError(ValueError):
    """The packaged model manifest is malformed or cannot be trusted."""


class ModelDownloadError(RuntimeError):
    """A managed model could not be downloaded and verified."""


def _validate_model_name(name: object) -> str:
    if not isinstance(name, str) or not _MODEL_NAME_RE.fullmatch(name):
        raise ManifestError(f"Invalid model name: {name!r}")
    return name


def _validate_filename(filename: object) -> str:
    if not isinstance(filename, str) or not filename:
        raise ManifestError(f"Invalid model filename: {filename!r}")
    if (
        filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or ":" in filename
        or "\x00" in filename
        or Path(filename).name != filename
    ):
        raise ManifestError(f"Unsafe model filename: {filename!r}")
    return filename


def validate_manifest(manifest: object) -> dict[str, dict[str, object]]:
    """Validate and return the model entries from a manifest object.

    Keys beginning with an underscore are metadata (the current manifest uses
    ``_comment``) and are intentionally ignored.  Every actual model entry
    must contain exactly the fields consumed by the downloader, including a
    full immutable commit revision and lower-case SHA-256 values under the
    canonical ``sha256`` field.
    """

    if not isinstance(manifest, dict):
        raise ManifestError("Model manifest must be a JSON object")

    models: dict[str, dict[str, object]] = {}
    for name, raw_entry in manifest.items():
        if not isinstance(name, str):
            raise ManifestError(f"Invalid model manifest key: {name!r}")
        if name.startswith("_"):
            continue
        _validate_model_name(name)
        if not isinstance(raw_entry, dict):
            raise ManifestError(f"Manifest entry {name!r} must be an object")
        if set(raw_entry) != _REQUIRED_ENTRY_FIELDS:
            raise ManifestError(
                f"Manifest entry {name!r} must contain repo_id, revision, and sha256"
            )

        repo_id = raw_entry["repo_id"]
        if (
            not isinstance(repo_id, str)
            or not repo_id
            or repo_id != repo_id.strip()
            or "\x00" in repo_id
        ):
            raise ManifestError(f"Invalid repo_id for model {name!r}")

        revision = raw_entry["revision"]
        if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
            raise ManifestError(
                f"Model {name!r} must use a 40-character lower-case commit revision"
            )

        raw_hashes = raw_entry["sha256"]
        if not isinstance(raw_hashes, dict) or not raw_hashes:
            raise ManifestError(f"Model {name!r} must declare file hashes")
        hashes: dict[str, str] = {}
        for filename, digest in raw_hashes.items():
            filename = _validate_filename(filename)
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise ManifestError(
                    f"Invalid SHA-256 for {name!r}/{filename!r}"
                )
            hashes[filename] = digest

        models[name] = {
            "repo_id": repo_id,
            "revision": revision,
            "sha256": hashes,
        }

    if not models:
        raise ManifestError("Model manifest contains no model entries")
    return models


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, dict[str, object]]:
    """Load and validate the packaged manifest through ``importlib.resources``."""

    resource = resources.files(_MANIFEST_PACKAGE)
    for part in _MANIFEST_RESOURCE:
        resource = resource.joinpath(part)
    try:
        with resource.open("r", encoding="utf-8") as stream:
            manifest = json.load(stream)
    except json.JSONDecodeError as exc:
        raise ManifestError("Packaged model manifest is not valid JSON") from exc
    except OSError as exc:
        raise ManifestError("Packaged model manifest could not be read") from exc
    return validate_manifest(manifest)


def cache_root() -> Path:
    """Return the per-user managed model cache root."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path(os.path.expanduser("~"))
    return base / "WhisperPaste" / "models"


def _snapshot_download(**kwargs):
    """Import Hugging Face lazily so local/bundled models need no network setup."""

    from huggingface_hub import snapshot_download

    return snapshot_download(**kwargs)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _required_files_present(directory: Path, hashes: dict[str, str]) -> bool:
    """Check a cache hit without reading model contents.

    The exact top-level layout is checked cheaply so stale Hugging Face cache
    metadata or an unexpected payload cannot be mistaken for a managed entry.
    Hashes are deliberately not calculated here; they are checked only when a
    directory is downloaded or replaced.
    """

    if not directory.is_dir() or directory.is_symlink():
        return False
    try:
        children = list(directory.iterdir())
    except OSError:
        return False
    expected = set(hashes)
    if {child.name for child in children} != expected:
        return False
    return all(
        child.is_file() and not child.is_symlink()
        for child in children
    )


def _verify_download(directory: Path, hashes: dict[str, str]) -> None:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("download did not produce a model directory")

    expected = set(hashes)
    children = list(directory.iterdir())
    actual = {child.name for child in children}
    unexpected = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unexpected:
        raise ValueError(f"unexpected model file(s): {', '.join(unexpected)}")
    if missing:
        raise ValueError(f"missing model file(s): {', '.join(missing)}")

    for filename, expected_digest in hashes.items():
        path = directory / filename
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing model file: {filename}")
        actual_digest = _sha256_file(path)
        if not hmac.compare_digest(actual_digest, expected_digest):
            raise ValueError(f"checksum mismatch for model file: {filename}")


def _staging_prefix(model_name: str, revision: str) -> str:
    return f".{model_name}.{revision}.staging-"


def _cleanup_staging(parent: Path, prefix: str) -> None:
    if not parent.is_dir():
        return
    for candidate in parent.glob(f"{prefix}*"):
        _remove_path(candidate)


def _concise_exception(exc: Exception) -> str:
    message = str(exc).splitlines()[0].strip()
    return message[:120] or exc.__class__.__name__


def ensure_model(model_name: str) -> Path:
    """Return a complete managed model directory, downloading if necessary."""

    manifest = load_manifest()
    entry = manifest.get(model_name)
    if entry is None:
        raise ModelDownloadError(
            f"Model {model_name!r} is not in the trusted manifest; "
            "choose a supported model or provide an existing local directory."
        )

    revision = entry["revision"]
    hashes = entry["sha256"]
    repo_id = entry["repo_id"]
    assert isinstance(revision, str)
    assert isinstance(hashes, dict)
    assert isinstance(repo_id, str)

    parent = cache_root() / model_name
    final = parent / revision
    if _required_files_present(final, hashes):
        return final

    staging: Path | None = None
    prefix = _staging_prefix(model_name, revision)
    try:
        parent.mkdir(parents=True, exist_ok=True)
        _cleanup_staging(parent, prefix)
        staging = Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))
        _snapshot_download(
            repo_id=repo_id,
            revision=revision,
            local_dir=str(staging),
            allow_patterns=sorted(hashes),
        )

        # snapshot_download stores its bookkeeping under local_dir/.cache;
        # it is not part of the model payload and must not be published.
        _remove_path(staging / ".cache")
        _verify_download(staging, hashes)

        # Keep a complete existing entry if another process won the race while
        # this download was running.  Otherwise replace only after verification.
        if _required_files_present(final, hashes):
            return final
        if final.exists() or final.is_symlink():
            _remove_path(final)
        os.replace(staging, final)
        staging = None
        return final
    except Exception as exc:
        raise ModelDownloadError(
            f"Unable to download model {model_name!r}: {_concise_exception(exc)}"
        ) from exc
    finally:
        if staging is not None:
            _remove_path(staging)


__all__ = [
    "ManifestError",
    "ModelDownloadError",
    "cache_root",
    "ensure_model",
    "load_manifest",
    "validate_manifest",
]
