"""Download one faster-whisper model at a pinned revision.

Run with the build venv's Python (it has the pinned huggingface_hub), from
scripts\\build.ps1:

    python packaging\\fetch_model.py whisper_paste\\resources\\models.json small build\\models\\small

Only downloads. Verification is build.ps1's job, done separately with
Get-FileHash so the check does not share a failure mode with the downloader.

allow_patterns is derived from the exact filenames in the trusted manifest, so the directory
this produces is what the normal download path would have produced — that is
what makes it safe to hand straight to WhisperModel as a verified directory.
"""

import json
import os
from pathlib import Path
import shutil
import sys


def _expected_files(entry):
    hashes = entry.get("sha256")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("manifest entry has no sha256 filenames")

    filenames = sorted(hashes)
    for filename in filenames:
        if not isinstance(filename, str) or not filename:
            raise ValueError("manifest contains an empty payload filename")
        normalized = filename.replace("\\", "/")
        if (
            normalized.startswith("/")
            or ":" in normalized
            or "/" in normalized
            or any(part in ("", ".", "..") for part in normalized.split("/"))
        ):
            raise ValueError(f"manifest contains an unsafe payload filename: {filename!r}")
    return filenames


def _remove_huggingface_cache(dest):
    root = Path(dest)
    if not root.exists():
        return
    for cache_dir in sorted(root.rglob(".cache"), key=lambda path: len(path.parts), reverse=True):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir)
        elif cache_dir.exists():
            cache_dir.unlink()


def _payload_files(dest):
    root = Path(dest)
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _validate_payload(dest, expected_files):
    actual_files = _payload_files(dest)
    expected = set(expected_files)
    missing = sorted(expected - actual_files)
    unexpected = sorted(actual_files - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise ValueError("downloaded model payload rejected (" + "; ".join(details) + ")")


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    manifest_path, name, dest = argv

    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    entry = manifest.get(name)
    if not isinstance(entry, dict):
        known = sorted(k for k in manifest if not k.startswith("_"))
        print(
            f"Unknown model '{name}'. Known models: {', '.join(known)}",
            file=sys.stderr,
        )
        return 1

    # A branch name here would silently defeat the pinning, so refuse anything
    # that is not a full commit SHA rather than trusting the manifest.
    revision = entry.get("revision", "")
    if len(revision) != 40 or not all(c in "0123456789abcdef" for c in revision):
        print(
            f"Model '{name}' has revision {revision!r}, which is not a 40-character "
            "commit SHA. Pin a commit, never a branch.",
            file=sys.stderr,
        )
        return 1

    try:
        expected_files = _expected_files(entry)
    except ValueError as exc:
        print(f"Model '{name}' has an invalid manifest payload: {exc}", file=sys.stderr)
        return 1

    # Imported here, not at module scope, so the argument and manifest errors
    # above report cleanly even if the build venv is half-installed.
    from huggingface_hub import snapshot_download

    os.makedirs(dest, exist_ok=True)
    print(f"Downloading {entry['repo_id']} @ {revision[:12]} -> {dest}")
    try:
        snapshot_download(
            entry["repo_id"],
            revision=revision,
            local_dir=dest,
            allow_patterns=expected_files,
        )
    finally:
        # huggingface_hub uses this directory for local_dir metadata. It is
        # not a model input and must never enter the portable payload.
        _remove_huggingface_cache(dest)

    try:
        _validate_payload(dest, expected_files)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Download complete.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 - build script wants a plain message
        print(f"Model download failed: {exc}", file=sys.stderr)
        sys.exit(1)
