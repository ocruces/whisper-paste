# Third-party notices — WhisperPaste portable build

WhisperPaste itself is MIT licensed; see `LICENSE` at the root of the ZIP.

The portable build is a PyInstaller bundle, so it ships compiled copies of its
Python dependencies and their native libraries inside `_internal\`. This file
records what that means for the two dependencies with copyleft obligations, and
points at where the full licence texts live.

## Where the licence texts are

**`licenses\` is the authoritative list, not this file.**

`scripts\build.ps1` populates `licenses\` directly from the build venv, copying
the `LICENSE` / `COPYING` / `NOTICE` files out of each installed distribution's
`*.dist-info` directory into `licenses\<package>\`. That means the licence texts
shipped in a ZIP are the ones belonging to the exact versions in that ZIP —
they are generated from `requirements-build.txt` at build time rather than
transcribed here by hand, where they would drift out of date silently.

If a package is in the bundle, its licence is in `licenses\`. Read those files
for the binding terms; the summaries below are orientation, not a substitute.

This document is a good-faith attribution notice written by the project
maintainers. It has not been reviewed by a lawyer, and nothing here is legal
advice.

## pystray — LGPL v3

Version: **0.19.5**. Copyright Moses Palmér. Licence text: `licenses\pystray\`.

pystray draws the tray icon and its menu. It is the one Python dependency under
a copyleft licence, and PyInstaller statically merges it into the executable, so
the LGPL's "user can replace the library" requirement has to be met some other
way than by swapping a DLL.

We meet it as follows:

- **The complete corresponding source of pystray 0.19.5 as bundled is included**
  in this ZIP, under `licenses\pystray\src\`. It is the unmodified upstream
  release; WhisperPaste patches nothing in it.
- **The relinking mechanism is public and reproducible.** `scripts\build.ps1` in
  the WhisperPaste repository builds this ZIP from `requirements-build.txt` and
  `packaging\whisper-paste.spec`, both of which ship in the source tree. To
  produce a bundle with a modified or newer pystray, change its pin in
  `requirements-build.txt` and re-run that script; no private tooling, key or
  build server is involved.

## FFmpeg, via PyAV — LGPL v2.1 or later

PyAV itself is **BSD 3-Clause** (licence text: `licenses\av\`). The obligation
comes from what its wheel carries: `_internal\av.libs\` contains roughly 62 MB
of prebuilt **FFmpeg** shared libraries (`avcodec`, `avformat`, `avutil`,
`swresample`, `swscale` and their dependencies), which are licensed under the
**GNU Lesser General Public License, version 2.1 or later**.

WhisperPaste reaches them only indirectly — `faster_whisper`'s audio module
imports `av` at module scope — but they are redistributed all the same.

The obligation has the same shape as pystray's, and is met the same way:

- The FFmpeg libraries are shipped **unmodified**, exactly as they came from the
  upstream PyAV wheel (`av==18.0.0`). WhisperPaste neither patches nor
  recompiles them.
- They are shipped as **separate DLLs** in `_internal\av.libs\`, dynamically
  loaded, so they can be replaced in place.
- The LGPL text and PyAV's own notices are in `licenses\av\`, and the build is
  reproducible from the public `scripts\build.ps1` as described above.

Note that FFmpeg can also be built with GPL-only components enabled. The PyAV
wheels used here are the standard LGPL builds; the licence and configuration
files under `licenses\av\` are what govern.

## Everything else

The remaining dependencies — the Whisper runtime (`faster-whisper`,
`ctranslate2`, `tokenizers`, `onnxruntime`), audio and input (`sounddevice` and
its bundled PortAudio, `keyboard`), imaging (`Pillow`), Windows integration
(`pywin32`), the HuggingFace client stack, and the Python standard library
embedded by PyInstaller — are all under permissive licences (variously MIT,
BSD, Apache-2.0, HPND and the PSF licence).

They are not enumerated individually here on purpose: a hand-maintained table
would go stale the first time a pin moves, and would assert licence names that
nobody re-checked. `licenses\` is generated per build and is the list that
matches what you actually received.

## Whisper models

Model weights are **not** covered by anything above. The `models\` directory is
populated after the build from the pinned HuggingFace repositories listed in
`packaging\models.json` (the `Systran/faster-whisper-*` conversions of OpenAI's
Whisper, MIT licensed). If your ZIP contains a `models\` directory, the terms
for those weights are the ones stated on the corresponding HuggingFace model
page.
