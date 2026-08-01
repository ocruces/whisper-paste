# Security review: the portable ZIP build

Status: complete, no reportable vulnerabilities. Reviewed 2026-08-01, against
commit `b7cf32a` on `feat/portable-zip` ("Add a portable ZIP build, with a
settings file and language launchers").

This document is self-contained — it assumes no knowledge of the conversation
that produced it. It is a companion to `docs/security-hardening-spec.md`, which
reviewed the shipped source app in July 2026; this one reviews only what the
portable-ZIP branch added.

---

## 0. Why this review happened

Until this branch, WhisperPaste was source-only: a clone, a venv, `python -m
whisper_paste`. The branch makes it the first thing this project **ships as a
binary to other people** — a self-contained ZIP with a PyInstaller onedir bundle,
the Whisper model inside it, a `whisper-paste.ini` settings file, and generated
`WhisperPaste-<lang>.cmd` launchers. That is a new class of artifact and a new
class of consequence: a defect in the source app harms the developer who wrote
it, whereas a defect in the ZIP harms everyone who downloads it.

Three areas were reviewed with that in mind:

1. **New parsers.** `whisper_paste/settings.py` reads an INI file that ships next
   to the exe — the first time the app takes configuration from a file.
2. **New path resolution.** `whisper_paste/bundle.py` resolves a model directory
   from `sys.executable`, and `transcriber._resolve_faster_whisper_model()`
   feeds the result to `WhisperModel`.
3. **The build pipeline as a supply chain.** `scripts/build.ps1`,
   `requirements-build.txt`, `packaging/whisper-paste.spec`,
   `packaging/fetch_model.py`, `packaging/models.json`, and the two `.cmd`
   templates — everything that decides what ends up inside the ZIP.

## 1. Threat model

The model in `docs/security-hardening-spec.md` §"Threat model assumed" still
applies and is not restated here. It assumes a single-user Windows machine and
explicitly does **not** treat other local user accounts as adversaries; the
adversaries it names are (a) processes running as the same user that harvest
durable files, (b) anything that later reads files the app leaves behind, and
(c) whatever is listening on `127.0.0.1:11434` when `--refine` is enabled.

The portable ZIP does not change that model, but it does widen **who runs the
code**. Two consequences are worth stating plainly rather than discovering later:

- The audience is no longer the developer. Users of the ZIP did not choose the
  dependency set, cannot read the build script before it runs, and mostly cannot
  tell a good binary from a bad one. That is what raises the stakes on the build
  pipeline (§3) even where the concrete risk is low.
- The install location is now the user's choice, made in Explorer, with no
  installer to constrain it. The app does not check the ACL of the directory it
  runs from, and does not attempt to. See §4.

## 2. Findings

**None reportable.** Three candidates were raised during the review and all
three were investigated and rejected. The reasoning is recorded here because the
*why not* is the part worth keeping — otherwise the same three get re-raised at
the next review.

| # | Candidate | Verdict |
|---|-----------|---------|
| 1 | Binary/DLL planting in the extraction folder | Rejected — needs a different local user account, which the threat model de-scopes; not materially different from the pre-existing clone + `.venv` |
| 2 | `whisper-paste.ini` read from the exe directory ahead of `%LOCALAPPDATA%` | Rejected — anyone who can write that file can replace the exe outright; no privilege boundary is crossed |
| 3 | `pip install` without `--require-hashes` | Rejected as a vulnerability; adopted as a hardening change |

### 2.1 Binary/DLL planting in the extraction folder

**The claim.** The ZIP contains an exe plus a writable `_internal\` payload of
DLLs and `.pyd` modules loaded on every launch, and the README says to unzip it
"wherever you like". A user who extracts to `C:\WhisperPaste` gets a directory
that inherits the root's `Authenticated Users:(M)` ACE, so any other local
account can overwrite the payload and run code as the victim — as administrator,
if the victim followed the troubleshooting advice about elevation.

**Why it was rejected.** The attacker is a *different local user account*, which
`security-hardening-spec.md` explicitly designates as out of scope. The in-scope
same-user adversary already executes as the victim and gains nothing by planting
a DLL. Nor is this new: the source install put a `.venv` full of DLLs and `.pyd`
files in a user-chosen directory too, and that spec's own Finding #1 records the
repository living under `C:\data` in the real deployment. The branch reproduces
an existing, already-accepted property rather than introducing one.

**What was done anyway.** The README now recommends extracting under a
user-owned path. That is deployment guidance, not a fix.

### 2.2 Settings file precedence

**The claim.** `settings.search_paths()` returns `<exe dir>\whisper-paste.ini`
before `%LOCALAPPDATA%\WhisperPaste\whisper-paste.ini`, first existing file
wins, and `build.ps1` always stages a copy next to the exe — so the
ACL-protected per-user location is unreachable by default. Two of the seven keys
have security-relevant effects: `log_dir` accepts any string including a UNC
path, and `log_transcripts` turns on durable plaintext logging of everything
dictated.

**Why it was rejected.** Writing that file requires the same access that would
let an attacker replace `WhisperPaste.exe` itself, add a Run key, or open the
microphone directly — all strictly stronger than editing an INI. No privilege
boundary is crossed, so inverting the search order would buy nothing. `log_dir`
is not a meaningful arbitrary-write primitive either: it produces a directory
and a file named `whisper-paste.log` containing app-generated text, under a
token that could already write anywhere, and `--log-dir` with identical
semantics predates this branch.

**What was done anyway.** `log_transcripts` is the one part with real substance:
it used to require a CLI flag at every launch and is now durable and silent. The
tray tooltip now says when transcript logging is on, which restores the property
that a user can tell. The precedence order itself was left alone — it is
deliberate, documented in `CLAUDE.md` §"Settings file", and the shipped template
explains it.

### 2.3 Unhashed build dependencies

**The claim.** `scripts/build.ps1` installs the pinned wheels that become
`WhisperPaste.exe` with `pip install --only-binary :all: -r requirements-build.txt`
— no `--require-hashes`, no index pinning, and an unpinned `pip install
--upgrade pip` before it. Meanwhile the model is pinned to a commit SHA *and*
SHA-256-verified independently. `BUILD-INFO.txt` records the hash of the
requirements *list*, not of the resolved wheels, so a swapped wheel produces an
identical BUILD-INFO line.

**Why it was rejected as a vulnerability.** The exploit path runs through
`PIP_INDEX_URL` or a `pip.ini` on the maintainer's own build machine — a trusted
environment, not an attacker-controlled one. The residual path is a PyPI or CDN
compromise, which `==` pins already blunt for the ordinary malicious-release
case, and which is shared by essentially every Python build in existence.
Version-only pinning is the norm, not a defect.

**What was done anyway.** It was adopted regardless, because the asymmetry was
real and the artifact goes to third parties: `requirements-build.txt` now
carries `--hash` lines, and `build.ps1` installs with `--require-hashes
--no-deps --isolated`. This closes the gap between what the README claims about
reproducibility and what the pipeline could actually demonstrate.

## 3. Controls that were checked and held

Recorded so a future review does not have to re-derive them, and so that a
change to any of these is understood as a change to a control:

- **Language codes** passed to `build.ps1 -Languages` are validated against
  `^[A-Za-z]{2,8}(-[A-Za-z0-9]{2,8})?$` *before* the venv, download and
  PyInstaller work. Each code lands both in a generated filename and on a
  command line inside that file, so this validation is the thing that keeps the
  generated `.cmd` launchers free of injection.
- **Model revisions** must be a 40-character hex commit SHA;
  `packaging/fetch_model.py` refuses a branch name outright rather than trusting
  the manifest. A branch would silently defeat the pinning.
- **Model contents** are SHA-256-verified by `build.ps1` in PowerShell, after
  `huggingface_hub` has downloaded them — deliberately not by the library that
  fetched them, so the check does not share a failure mode with the downloader.
  A mismatch deletes the cache and fails the build.
- **Both `.cmd` templates** resolve the exe through `%~dp0` (their own
  directory), never through the working directory or `PATH`.
- **No dangerous primitives** in the new code: no `subprocess`, `eval`, `exec`,
  `pickle`, `yaml.load`, or `Invoke-Expression` anywhere in the diff.
- **`configparser`** is constructed with `interpolation=None`, so values are
  taken literally; nothing in the settings path evaluates anything.
- **`pywhispercpp` exclusion** is enforced three independent ways (absent from
  the pins, named in the spec's `excludes`, asserted by a post-build gate), so a
  contaminated venv cannot leak the whisper.cpp runtime into a shipped ZIP.
- **`upx=False`** stays False. Packing is an AV heuristic trigger and this app
  already looks keylogger-shaped.

One genuine defect was found in this area and is recorded here so the guard's
history is legible:
`bundle.bundled_model_dir()` rejected separators, absolute paths and `.`/`..`,
but not a bare drive prefix such as `C:x` — which `ntpath.join` collapses, so on
any install drive other than `C:` the lookup resolved against the current
directory on `C:` instead of the bundle. The value only ever comes from the
user's own `--model` or their own INI. Fixed with `os.path.splitdrive`, with a
test.

## 4. Accepted residual risks

Stated plainly, so that nobody later mistakes silence for absence:

- **The executable is not code-signed.** SmartScreen will warn on first launch
  and some AV engines will flag the binary. This is a documented, accepted
  product decision — see the README, which offers the published SHA-256,
  `BUILD-INFO.txt`, and a public reproducible build script as the evidence a
  user can actually check, and points anyone unwilling to run an unsigned binary
  at the source install.
- **The extraction location is the user's choice and its ACL is not checked.**
  The app does not inspect the permissions of the directory it runs from and
  will start happily from a world-writable one. The README recommends a
  user-owned path; nothing enforces it.
- **`whisper-paste.ini` beside the exe wins over the per-user copy.** By design,
  so that a user can edit the file they can see. The per-user path is only
  reachable if the shipped one is removed.
- **`log_transcripts` is durable once set.** Off by default, warned about in the
  shipped template, and now visible in the tray tooltip while active — but a
  setting that persists across reboots is still a setting someone can forget
  they enabled.
- **The runtime HuggingFace download fallback still exists.** `--model
  <something-not-bundled>` reaches out to the network at load time. It is
  `certifi`-backed TLS to a repo id the user typed, and it is the same code path
  the source install has always used.

## 5. If you are reviewing this branch again

Re-verify rather than assume, in roughly this order of value:

1. `packaging/models.json` — that every `revision` is still a 40-char commit SHA
   and every listed file still has a SHA-256.
2. `requirements-build.txt` — that every pin still carries a `--hash`, and that
   `build.ps1` still passes `--require-hashes`. `tests/test_packaging.py` covers
   both; make sure the test is still meaningful, not just still green.
3. The `-Languages` regex, if the launcher template ever grows a second
   substitution point.
4. `settings.py` `_KEYS`, if a new key is added — ask what the worst legal value
   of it does, and whether the answer is visible to the user.
