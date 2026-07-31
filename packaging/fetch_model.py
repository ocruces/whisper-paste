"""Download one faster-whisper model at a pinned revision.

Run with the build venv's Python (it has the pinned huggingface_hub), from
scripts\\build.ps1:

    python packaging\\fetch_model.py packaging\\models.json small build\\models\\small

Only downloads. Verification is build.ps1's job, done separately with
Get-FileHash so the check does not share a failure mode with the downloader.

allow_patterns mirrors faster_whisper/utils.py:91-97 exactly, so the directory
this produces is what the normal download path would have produced — that is
what makes it safe to hand straight to WhisperModel as a directory.
"""

import json
import os
import sys

ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]


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

    # Imported here, not at module scope, so the argument and manifest errors
    # above report cleanly even if the build venv is half-installed.
    from huggingface_hub import snapshot_download

    os.makedirs(dest, exist_ok=True)
    print(f"Downloading {entry['repo_id']} @ {revision[:12]} -> {dest}")
    snapshot_download(
        entry["repo_id"],
        revision=revision,
        local_dir=dest,
        allow_patterns=ALLOW_PATTERNS,
    )
    print("Download complete.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 - build script wants a plain message
        print(f"Model download failed: {exc}", file=sys.stderr)
        sys.exit(1)
