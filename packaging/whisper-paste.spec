# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the portable WhisperPaste ZIP (onedir).

Run it from scripts\\build.ps1, with the *build* venv's Python:

    build\\venv\\Scripts\\python.exe -m PyInstaller --noconfirm \\
        --distpath dist --workpath build\\pyinstaller packaging\\whisper-paste.spec

Produces dist\\WhisperPaste\\ containing two executables that share one
_internal payload — see the EXE section below for why there are two.

The Whisper model is deliberately NOT in `datas`; scripts\\build.ps1 copies it
into dist\\WhisperPaste\\models\\<name> after PyInstaller has finished. Three
reasons, all of them load-bearing:

  1. Cost. A model is 461 MB for `small`. Analysis hashes every datas entry and
     COLLECT copies it again, so putting it here pays that twice on every
     build, including builds where only a .py changed.
  2. Layout. PyInstaller 6.x puts *all* datas under `_internal\\`, and a dest
     path containing `..` is rejected outright. The design (whisper_paste/
     bundle.py:bundled_model_dir) puts the model next to the exe, not under
     _internal, precisely so a user can drop their own converted model into
     models\\my-model\\ and reach it with `--model my-model`. There is no way to
     express that destination from `datas`.
  3. Model-agnosticism. Keeping it out means `build.ps1 -Model medium` needs no
     edit to this file. The spec describes the *program*; the model is cargo.
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# SPECPATH is packaging\, so the repo root is its parent. Deriving it rather
# than assuming the CWD means the spec works no matter where PyInstaller is
# invoked from.
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
PACKAGING = os.path.join(REPO_ROOT, "packaging")

# The mandatory data directives.
#
# faster_whisper/vad.py locates the Silero VAD model with a path computed
# relative to its own __file__ (get_assets_path() -> faster_whisper/assets/),
# which PyInstaller's static analysis cannot see. Without this, the frozen
# build has no faster_whisper/assets/silero_vad_v6.onnx and the very first real
# dictation dies with NoSuchFile — transcriber.py passes vad_filter=True on
# every faster-whisper transcription, so this is the hot path, not an edge
# case. Verified empirically: with the directive, the path get_assets_path()
# computes under _MEIPASS is exactly where the asset lands.
datas = collect_data_files("faster_whisper")

# The runtime model cache and the build use this same packaged manifest. Keep
# it in the frozen package so a source/frozen install cannot silently drift.
_MANIFEST = os.path.join(REPO_ROOT, "whisper_paste", "resources", "models.json")
datas.append((_MANIFEST, os.path.join("whisper_paste", "resources")))

# Empty on purpose. Every one of the following was tried in a probe build and
# proven REDUNDANT — the hooks already shipped with PyInstaller and
# pyinstaller-hooks-contrib do them, and repeating them here only creates
# duplicate work and a second place to get stale. Do not "helpfully" add them:
#
#   collect_dynamic_libs("ctranslate2")   - resolved without help; see below
#   collect_dynamic_libs("onnxruntime")   ] contrib hook-onnxruntime.py
#   collect_data_files("onnxruntime")     ]
#   collect_data_files("sounddevice")     ] contrib hook-sounddevice.py
#   collect_data_files("_sounddevice_data")]  (ships the PortAudio DLL)
#   hiddenimports "_cffi_backend"         - pulled in by the sounddevice hook
#   hiddenimports "pystray._win32"        - contrib hook-pystray.py
#
# Also relevant and already handled: contrib hook-av.py, and PyInstaller's own
# hook-numpy.py / hook-PIL*.py.
#
# Specifically NOT needed either: a runtime hook for ctranslate2. The worry was
# that importlib.resources.files("ctranslate2") would not resolve in a frozen
# process; measured on PyInstaller 6.21 it resolves to _MEIPASS\\ctranslate2,
# both DLLs are present, and the model loads. No rthook_ctranslate2.py exists,
# and none should be written.
binaries = []

# Insurance, honestly labelled.
#
# huggingface_hub 1.x is a lazy-loading package: its __init__ defines a
# _SUBMOD_ATTRS map and resolves names via module __getattr__ on first access,
# so PyInstaller's import graph sees almost nothing. The frozen app reaches it
# only on the manifest-limited download path — a manifest-listed model that is not bundled, where
# model_cache.py calls snapshot_download(). The spike did not exercise that path,
# and a ModuleNotFoundError there is invisible to every unit test in this repo
# (they all stub the model). Collecting the submodules costs a few MB and
# removes a failure mode that would otherwise first appear on a user's machine.
# This is deliberate insurance, not cargo cult; the manifest-limited download path is
# verified against a built bundle before the ZIP ships.
hiddenimports = collect_submodules("huggingface_hub")

excludes = [
    # pywhispercpp is excluded here AND absent from requirements-build.txt.
    # That is not redundancy — it is two independent guards against the same
    # accident. If someone builds in a venv that is not the clean build venv
    # (an activated .venv, a `pip install -e .` that dragged the dependency
    # in), the requirements pin has no say and only this line keeps the
    # whisper.cpp runtime out of the ZIP. Conversely, if this spec is edited,
    # the pin still keeps it out of a clean build. The portable build rejects
    # --gpu outright (whisper_paste/bundle.py:GPU_UNSUPPORTED_MESSAGE), so a
    # bundled pywhispercpp would be dead weight that could never be reached.
    "pywhispercpp",
    # Stdlib and tooling this app never touches. pystray uses win32, not Tk.
    "tkinter",
    "unittest",
    "pydoc_data",
    "lib2to3",
    "setuptools",
    "pip",
    # NOT excluded, on purpose: hf_xet. huggingface_hub uses it to fetch
    # Xet-backed repos, which is how the Systran/faster-whisper-* repos are
    # served, so removing it risks breaking the manifest-limited download path the
    # hiddenimports above exist to insure. 9 MB is not worth an untested
    # failure mode.
    #
    # NOT excluded either: av. faster_whisper/__init__.py imports audio.py,
    # which does a top-level `import av` — excluding it breaks every
    # faster_whisper import, verified. It costs ~75 MB (av.libs is FFmpeg) and
    # replacing it with a stub package is a possible follow-up, not this
    # build's business.
]

a = Analysis(
    [os.path.join(PACKAGING, "whisper_paste_launcher.py")],
    # The repo root, so `import whisper_paste` resolves from a plain checkout
    # without `pip install -e .` first. The build venv deliberately does not
    # have the app installed — it has only the pinned dependencies.
    pathex=[REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

_ICON = os.path.join(PACKAGING, "whisper-paste.ico")
_VERSION = os.path.join(PACKAGING, "version_info.txt")

# Two executables, one Analysis, one COLLECT.
#
# A windowed (GUI-subsystem) exe launched from an existing console does NOT
# attach to it: measured, sys.stdout is None and GetConsoleWindow() returns 0.
# So there is no wrapper script, .cmd or otherwise, that can make WhisperPaste
# .exe print anything — the subsystem bit is decided at link time. The only way
# to give a user a readable crash is to link a second, console-subsystem copy
# of the same program. packaging\debug-console.cmd launches this twin.
#
# They share `pyz` and `a.scripts`, so the twin is genuinely the same program,
# and COLLECT emits one shared _internal payload: the second exe costs about a
# megabyte, not a second copy of the 243 MB bundle.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WhisperPaste",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is off deliberately, and stays off even on machines where it is not
    # installed (here it is not, so this is currently a no-op). Packed
    # executables are a well-known AV heuristic trigger, and this app already
    # looks keylogger-shaped to scanners — it installs a WH_KEYBOARD_LL hook
    # with suppress=True and writes the clipboard. See README, "Windows
    # SmartScreen and antivirus". Keep it False as insurance for build machines
    # that do have UPX on PATH.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
    version=_VERSION,
)

exe_debug = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WhisperPaste-debug",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
    version=_VERSION,
)

coll = COLLECT(
    exe,
    exe_debug,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WhisperPaste",
)
