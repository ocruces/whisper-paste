"""Awareness of the frozen (PyInstaller onedir) bundle.

Deliberately knows nothing about `config`: it answers "am I an exe, and what
shipped next to me", and the caller decides what to do with the answer. That
keeps it free of the import-order rules the rest of the package lives under
(see CLAUDE.md on config being mutated at startup) and makes it trivial to
unit-test by monkeypatching `sys`.
"""

import os
import sys

GPU_UNSUPPORTED_MESSAGE = (
    "--gpu is not available in the portable build.\n\n"
    "GPU/Vulkan transcription needs pywhispercpp compiled with GGML_VULKAN=1, "
    "which cannot be shipped as a prebuilt binary — the PyPI wheel is CPU-only. "
    "Install WhisperPaste from source and follow \"GPU support (AMD / "
    "non-NVIDIA)\" in the README:\n"
    "https://github.com/ocruces/whisper-paste#gpu-support-amd--non-nvidia"
)


def is_frozen():
    """True when running from a PyInstaller bundle.

    Both markers are required. `sys.frozen` alone is set by other freezers (and
    by py2exe-era shims) and says nothing about the layout; `sys._MEIPASS` is
    what actually tells us the bundle directory structure `bundled_model_dir`
    assumes below is real.
    """
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def bundled_model_dir(name):
    """Absolute path of a Whisper model shipped next to the exe, or None.

    onedir layout: the exe sits at the ZIP root and the model beside it in
    `models\\<name>` — *not* under `_internal`, where PyInstaller puts `datas`.
    The model is copied in after the build for that reason among others; see
    CLAUDE.md, "Packaging: the portable ZIP".

    Returns None — meaning the caller should continue its local/manifest resolution policy —
    when:

      - not frozen, so a source checkout keeps today's behaviour exactly;
      - `name` is empty;
      - `name` is not a bare model name. A path (``C:\\models\\x``) is
        handled by the caller as an explicit local directory, while a
        HuggingFace repo id (``Systran/faster-whisper-small``) is rejected by
        the manifest-backed resolver. Refusing separators is also what stops
        ``--model ..\\..\\x`` from aiming the lookup outside the bundle. A
        bare drive prefix like ``C:x`` needs its own check: it has no
        separator and `os.path.isabs` calls it relative, yet it is
        drive-relative, and `ntpath.join` collapses it — measured,
        ``ntpath.join(r'D:\\app', 'models', 'C:x')`` returns ``'C:x'``, not a
        path under ``models\\`` — so on any install drive other than `C:` it
        would resolve against the current directory on drive `C:` instead of
        the bundle. `os.path.splitdrive` catches this one;
      - nothing by that name shipped with this build — e.g. ``--model medium``
        on a ZIP built with ``-Model small``. The caller then consults the
        trusted manifest and rejects the name if it is not listed.

    Keying the directory on the model name is what lets `--model` keep working
    unchanged in a frozen build, and means a user who drops another converted
    model into `models\\my-model\\` reaches it with `--model my-model`.
    """
    if not is_frozen() or not name:
        return None
    if (
        os.path.isabs(name)
        or "/" in name
        or "\\" in name
        or name in (".", "..")
        or os.path.splitdrive(name)[0]
    ):
        return None
    path = os.path.join(
        os.path.dirname(os.path.abspath(sys.executable)), "models", name
    )
    return path if os.path.isdir(path) else None
