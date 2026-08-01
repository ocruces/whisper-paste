<#
.SYNOPSIS
    Builds the portable WhisperPaste ZIP: a self-contained folder with the
    Whisper model inside, for users who do not have Python.
.DESCRIPTION
    From a clean clone this is the only command needed. It creates an isolated
    build venv (never touching .venv), installs the pinned build requirements,
    downloads and SHA-256-verifies the Whisper model, runs PyInstaller, checks
    the result, stages licences and docs, and produces a versioned ZIP.

    The build venv lives in build\venv so a contributor's .venv - which may hold
    unpinned or extra packages such as pywhispercpp - can never leak into a
    shipped artifact.
.PARAMETER Model
    Which model to bundle. Must be a key in packaging\models.json. Default: small.
.PARAMETER Clean
    Remove the build venv, the PyInstaller work directory and the previous dist
    tree first. Never removes build\models - that is the download cache, and
    re-downloading 461 MB to rebuild a spec change is pure waste.
.PARAMETER SkipZip
    Produce dist\WhisperPaste but do not compress it. Useful while iterating.
.PARAMETER AllowPythonMismatch
    Continue even if the interpreter's minor version differs from BUILD_PYTHON
    in requirements-build.txt.
.PARAMETER WriteHashes
    Print manifest-ready SHA-256 JSON for the downloaded model instead of
    verifying it. For adding a new model to packaging\models.json.
.PARAMETER OutputDir
    Where to write the ZIP. Default: dist.
.PARAMETER Languages
    Comma-separated language codes to generate WhisperPaste-<code>.cmd
    launchers for, from packaging\launcher-template.cmd. WhisperPaste.exe is
    started by double-click, which cannot pass --lang, so each language needs
    its own one-line .cmd next to the exe. Default: en,es. Pass '' for none.
.EXAMPLE
    scripts\build.ps1
.EXAMPLE
    scripts\build.ps1 -Model base -Clean
.EXAMPLE
    scripts\build.ps1 -Languages en,es,fr,pt-br
#>
[CmdletBinding()]
param(
    [string]$Model = 'small',
    [switch]$Clean,
    [switch]$SkipZip,
    [switch]$AllowPythonMismatch,
    [switch]$WriteHashes,
    [string]$OutputDir,
    [string]$Languages = 'en,es'
)

# NOTE: deliberately not setting $ErrorActionPreference = 'Stop'. In Windows
# PowerShell 5.1 that turns redirected native stderr (e.g. pip WARNING lines)
# into terminating errors. $LASTEXITCODE is checked explicitly after every
# native call instead. Same reasoning as scripts\install.ps1.

$root = Split-Path -Parent $PSScriptRoot

$packaging       = Join-Path $root 'packaging'
$specPath        = Join-Path $packaging 'whisper-paste.spec'
$manifestPath    = Join-Path $packaging 'models.json'
$fetchScript     = Join-Path $packaging 'fetch_model.py'
$launcherTpl     = Join-Path $packaging 'launcher-template.cmd'
$settingsTpl     = Join-Path $packaging 'whisper-paste.ini'
$reqBuild        = Join-Path $root 'requirements-build.txt'
$buildDir        = Join-Path $root 'build'
$buildVenv       = Join-Path $buildDir 'venv'
$buildVenvPython = Join-Path $buildVenv 'Scripts\python.exe'
$workPath        = Join-Path $buildDir 'pyinstaller'
$modelCache      = Join-Path $buildDir 'models'
$distDir         = Join-Path $root 'dist'
$stageDir        = Join-Path $distDir 'WhisperPaste'

if (-not $OutputDir) { $OutputDir = $distDir }

Write-Host "WhisperPaste portable build" -ForegroundColor Cyan
Write-Host "Repo root: $root"

# ---------------------------------------------------------------- 1. context
$gitCommit = 'unknown'
if (Get-Command git -ErrorAction SilentlyContinue) {
    $c = & git -C $root rev-parse --short HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $c) { $gitCommit = $c.Trim() }
}

$appVersion = & {
    $initPath = Join-Path $root 'whisper_paste\__init__.py'
    $m = Select-String -Path $initPath -Pattern '^__version__\s*=\s*"([^"]+)"'
    if ($m) { $m.Matches[0].Groups[1].Value } else { '0.0.0' }
}
Write-Host "Version: $appVersion  Commit: $gitCommit  Model: $Model"

foreach ($required in @($specPath, $manifestPath, $fetchScript, $reqBuild,
                        $launcherTpl, $settingsTpl)) {
    if (-not (Test-Path $required)) {
        Write-Host "ERROR: missing build input: $required" -ForegroundColor Red
        exit 1
    }
}

# ------------------------------------------------------- 2. locate Python
function Test-PythonOk {
    param([string]$Exe, [string[]]$PreArgs = @())
    try {
        & $Exe @PreArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    } catch {
        return $false
    }
    return ($LASTEXITCODE -eq 0)
}

$pyExe = $null
$pyPreArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    if (Test-PythonOk -Exe 'py' -PreArgs @('-3')) { $pyExe = 'py'; $pyPreArgs = @('-3') }
}
if (-not $pyExe) {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        if (Test-PythonOk -Exe 'python') { $pyExe = 'python'; $pyPreArgs = @() }
    }
}
if (-not $pyExe) {
    Write-Host "ERROR: No Python 3.10+ found." -ForegroundColor Red
    Write-Host "Install Python 3.10 or newer from https://www.python.org/downloads/"
    exit 1
}

# requirements-build.txt pins package VERSIONS, not wheel tags - a different
# Python minor resolves different wheels for the same version strings, so the
# artifact would silently stop matching what was tested.
$buildPythonLine = Select-String -Path $reqBuild -Pattern '^#\s*BUILD_PYTHON\s*=\s*(\d+\.\d+)\s*$'
$actualMinor = (& $pyExe @pyPreArgs -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($buildPythonLine) {
    $wantMinor = $buildPythonLine.Matches[0].Groups[1].Value
    if ($actualMinor -ne $wantMinor) {
        Write-Host "ERROR: requirements-build.txt pins BUILD_PYTHON = $wantMinor but this is Python $actualMinor." -ForegroundColor Red
        Write-Host "Install Python $wantMinor, or pass -AllowPythonMismatch to build anyway."
        if (-not $AllowPythonMismatch) { exit 1 }
        Write-Host "Continuing anyway (-AllowPythonMismatch)." -ForegroundColor Yellow
    }
}
Write-Host "Using Python $actualMinor ($pyExe $($pyPreArgs -join ' '))" -ForegroundColor Green

# ------------------------------------------------------- 3. validate -Model
$manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$modelNames = @($manifest.PSObject.Properties |
    Where-Object { -not $_.Name.StartsWith('_') } | ForEach-Object { $_.Name })
if ($modelNames -notcontains $Model) {
    Write-Host "ERROR: unknown model '$Model'." -ForegroundColor Red
    Write-Host "Known models: $($modelNames -join ', ')"
    Write-Host "Add a new one with: scripts\build.ps1 -Model <name> -WriteHashes"
    exit 1
}
$modelEntry = $manifest.$Model

# --------------------------------------------------- 3b. validate -Languages
# Parsed and validated here, before the venv/download/PyInstaller work, so a
# typo costs a second rather than five minutes. Each code ends up both in a
# generated filename and on the command line inside that file, so it is checked
# against a strict shape - letters with an optional -region suffix - instead of
# being pasted through. Duplicates are collapsed and codes are lower-cased,
# which is the form Whisper expects; an empty list is legal and simply produces
# no launchers.
$langCodes = @()
foreach ($rawLang in ($Languages -split ',')) {
    $code = $rawLang.Trim()
    if (-not $code) { continue }
    if ($code -notmatch '^[A-Za-z]{2,8}(-[A-Za-z0-9]{2,8})?$') {
        Write-Host "ERROR: invalid language code '$code' in -Languages." -ForegroundColor Red
        Write-Host "Expected letters with an optional region suffix, e.g. en, es, pt-br."
        exit 1
    }
    $langCodes += $code.ToLower()
}
$langCodes = @($langCodes | Select-Object -Unique)
if ($langCodes.Count -gt 0) {
    Write-Host "Launcher languages: $($langCodes -join ', ')"
} else {
    Write-Host "Launcher languages: none (-Languages is empty)" -ForegroundColor Yellow
}

# ------------------------------------------------------------- 4. clean
if ($Clean) {
    Write-Host "Cleaning (build venv, PyInstaller work dir, dist tree) ..."
    foreach ($p in @($buildVenv, $workPath, $stageDir)) {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
    # build\models is intentionally preserved - see .PARAMETER Clean.
}

# --------------------------------------------------- 5/6. build venv + deps
$reqHash = (Get-FileHash $reqBuild -Algorithm SHA256).Hash
$stamp = Join-Path $buildVenv '.reqs.sha256'
$venvUsable = (Test-Path $buildVenvPython) -and (Test-Path $stamp) -and
              ((Get-Content $stamp -Raw).Trim() -eq $reqHash)

if ($venvUsable) {
    Write-Host "Build venv is current (requirements-build.txt unchanged) - reusing it." -ForegroundColor Yellow
} else {
    if (Test-Path $buildVenv) {
        Write-Host "Build requirements changed - recreating the build venv ..."
        Remove-Item -Recurse -Force $buildVenv
    }
    New-Item -ItemType Directory -Force $buildDir | Out-Null
    Write-Host "Creating build venv at $buildVenv ..."
    & $pyExe @pyPreArgs -m venv $buildVenv
    if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: venv creation failed." -ForegroundColor Red; exit 1 }

    # Pinned, not just upgraded: an unpinned `--upgrade pip` resolves whatever
    # is newest on the day someone runs -Clean, so the bootstrap step itself
    # would drift between two builds of the same commit - the exact drift this
    # whole file exists to remove from the requirements below it.
    & $buildVenvPython -m pip install --upgrade pip==26.2
    if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: pip upgrade failed." -ForegroundColor Red; exit 1 }

    # --only-binary :all: - a source build here would compile against whatever
    # toolchain the machine happens to have, which is the opposite of pinned.
    #
    # --require-hashes - requirements-build.txt now carries a --hash=sha256:...
    # per package, pinning the exact wheel bytes pip installs rather than just
    # a version string that a compromised or re-uploaded index could serve
    # different content under. --no-deps is required, not merely safe: the file
    # is a full pip-freeze transitive closure, but `pip freeze` deliberately
    # omits venv plumbing, and both ctranslate2 and pyinstaller declare an
    # unconditional Requires-Dist on setuptools. Without --no-deps, pip would
    # try to resolve setuptools, find no pin and no hash for it, and refuse the
    # whole install - hash-checking mode will not resolve anything it cannot
    # verify. With --no-deps it never looks, and the build works because
    # `python -m venv` on 3.11 still provisions setuptools itself. That is a
    # real dependency on BUILD_PYTHON staying at 3.11: 3.12+ dropped setuptools
    # from new venvs, so raising that marker means adding setuptools to the
    # pins, not just regenerating them. --isolated stops a stray
    # %APPDATA%\pip\pip.ini or an inherited PIP_INDEX_URL on the build machine
    # from quietly redirecting where these hashes get checked against - a
    # hash-checked install from the wrong index is not a security property,
    # it is theatre.
    Write-Host "Installing pinned build requirements ..."
    & $buildVenvPython -m pip install --only-binary :all: --require-hashes --no-deps --isolated -r $reqBuild
    if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: pinned install failed." -ForegroundColor Red; exit 1 }

    Set-Content -Path $stamp -Value $reqHash -Encoding utf8
}

# ------------------------------------------------- 7. pywhispercpp must be out
# Belt and braces with the spec's excludes: either alone would let the
# whisper.cpp runtime back in after a venv slip, and --gpu is a source-install
# feature that the frozen exe refuses outright.
& $buildVenvPython -c "import importlib.util,sys; sys.exit(1 if importlib.util.find_spec('pywhispercpp') else 0)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pywhispercpp is importable in the build venv." -ForegroundColor Red
    Write-Host "The build venv is stale or contaminated. Rerun with -Clean."
    exit 1
}

# ------------------------------------------------- 8. fetch + verify model
$modelDir = Join-Path $modelCache $Model
$expected = $modelEntry.sha256

function Test-ModelHashes {
    param([string]$Dir)
    foreach ($prop in $expected.PSObject.Properties) {
        $f = Join-Path $Dir $prop.Name
        if (-not (Test-Path $f)) { return $false }
        if ((Get-FileHash $f -Algorithm SHA256).Hash -ne $prop.Value.ToUpper()) { return $false }
    }
    return $true
}

if ((Test-Path $modelDir) -and (-not $WriteHashes) -and (Test-ModelHashes -Dir $modelDir)) {
    Write-Host "Model '$Model' already cached and verified." -ForegroundColor Yellow
} else {
    Write-Host "Downloading model '$Model' ($($modelEntry.repo_id)) ..."
    $env:HF_HUB_DISABLE_TELEMETRY = '1'
    $env:HF_HUB_DISABLE_PROGRESS_BARS = '1'
    & $buildVenvPython $fetchScript $manifestPath $Model $modelDir
    $fetchCode = $LASTEXITCODE
    Remove-Item Env:\HF_HUB_DISABLE_TELEMETRY -ErrorAction SilentlyContinue
    Remove-Item Env:\HF_HUB_DISABLE_PROGRESS_BARS -ErrorAction SilentlyContinue
    if ($fetchCode -ne 0) { Write-Host "ERROR: model download failed." -ForegroundColor Red; exit 1 }

    if ($WriteHashes) {
        Write-Host ""
        Write-Host "WARNING: -WriteHashes SKIPS VERIFICATION." -ForegroundColor Yellow
        Write-Host "Review these against huggingface.co before committing them." -ForegroundColor Yellow
        Write-Host ""
        $pairs = Get-ChildItem $modelDir -File | Sort-Object Name | ForEach-Object {
            '      "{0}": "{1}"' -f $_.Name, (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
        }
        Write-Host ('  "{0}": {{' -f $Model)
        Write-Host ('    "repo_id": "{0}",' -f $modelEntry.repo_id)
        Write-Host ('    "revision": "{0}",' -f $modelEntry.revision)
        Write-Host '    "sha256": {'
        Write-Host ($pairs -join ",`n")
        Write-Host '    }'
        Write-Host '  }'
        exit 0
    }

    # Verified independently of the downloader, in PowerShell rather than with
    # the same library that fetched it, so a corrupt or swapped file cannot ship.
    Write-Host "Verifying model checksums ..."
    if (-not (Test-ModelHashes -Dir $modelDir)) {
        Write-Host "ERROR: model checksum mismatch - refusing to ship it." -ForegroundColor Red
        Write-Host "Deleting $modelDir so the next run re-downloads."
        Remove-Item -Recurse -Force $modelDir
        exit 1
    }
}
Write-Host "Model verified against packaging\models.json." -ForegroundColor Green

# ------------------------------------------------------------ 9/10. build
# Always removed: a stale tree hides files the spec no longer produces.
if (Test-Path $stageDir) { Remove-Item -Recurse -Force $stageDir }

Write-Host "Running PyInstaller ..."
& $buildVenvPython -m PyInstaller --noconfirm --distpath $distDir --workpath $workPath $specPath
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: PyInstaller failed." -ForegroundColor Red; exit 1 }

# ------------------------------------------------------ 11. post-build gate
# Each of these maps to a specific runtime failure that would otherwise only
# surface on a user's machine as "the tray icon never turns green".
$required = @(
    'WhisperPaste.exe',
    'WhisperPaste-debug.exe',
    '_internal\faster_whisper\assets\silero_vad_v6.onnx',
    '_internal\ctranslate2\ctranslate2.dll',
    '_internal\onnxruntime\capi\onnxruntime_pybind11_state.pyd',
    '_internal\_sounddevice_data\portaudio-binaries\libportaudio64bit.dll'
)
$missing = @($required | Where-Object { -not (Test-Path (Join-Path $stageDir $_)) })
if ($missing.Count -gt 0) {
    Write-Host "ERROR: the bundle is missing required files:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  $_" }
    exit 1
}
if (Test-Path (Join-Path $stageDir '_internal\pywhispercpp')) {
    Write-Host "ERROR: pywhispercpp was bundled - the spec's excludes are not working." -ForegroundColor Red
    exit 1
}
Write-Host "Bundle sanity checks passed." -ForegroundColor Green

# --------------------------------------------------------- 12. model copy
Write-Host "Copying model into the bundle ..."
$modelDest = Join-Path $stageDir "models\$Model"
& robocopy $modelDir $modelDest /E /NFL /NDL /NJH /NJS /NP | Out-Null
# robocopy uses exit codes 0-7 for success (8+ are real failures).
if ($LASTEXITCODE -ge 8) { Write-Host "ERROR: model copy failed (robocopy $LASTEXITCODE)." -ForegroundColor Red; exit 1 }
$global:LASTEXITCODE = 0

# --------------------------------------------------------- 13. stage docs
Copy-Item (Join-Path $root 'README.md')  $stageDir -Force
Copy-Item (Join-Path $root 'LICENSE')    $stageDir -Force
Copy-Item (Join-Path $packaging 'debug-console.cmd') $stageDir -Force
Copy-Item (Join-Path $packaging 'THIRD-PARTY-NOTICES.md') $stageDir -Force
# Ships entirely commented out, so its presence changes no behaviour - it is a
# form the user only has to uncomment a line of.
Copy-Item $settingsTpl $stageDir -Force

# ------------------------------------------------- 13b. language launchers
# One WhisperPaste-<code>.cmd per requested language, expanded from the single
# checked-in template. Generated rather than committed so a new language needs
# no new file, and regenerated every run because step 9 wipes the stage dir.
$launchers = @()
foreach ($code in $langCodes) {
    $name = "WhisperPaste-$code.cmd"
    # Read as lines, not -Raw: Set-Content then rejoins them with CRLF, so the
    # generated .cmd has Windows line endings whatever the template was checked
    # out with. -Encoding ascii because cmd.exe mishandles a UTF-8 BOM at the
    # start of a batch file (it lands in the first command); PowerShell 5.1
    # would write one by default. The template is ASCII by design.
    $lines = (Get-Content $launcherTpl) -replace '@@LANG@@', $code
    Set-Content -Path (Join-Path $stageDir $name) -Value $lines -Encoding ascii
    $launchers += $name
}
if ($launchers.Count -gt 0) {
    Write-Host "Language launchers: $($launchers -join ', ')" -ForegroundColor Green
} else {
    Write-Host "No language launchers generated (-Languages is empty)." -ForegroundColor Yellow
}

# The step 11 gate cannot cover these: it runs immediately after PyInstaller,
# before anything is staged, so asserting them there would fail on a perfectly
# good build. Checked here, where they have actually been written.
$requiredStaged = @('whisper-paste.ini') + @($launchers | Select-Object -First 1)
$missingStaged = @($requiredStaged | Where-Object { -not (Test-Path (Join-Path $stageDir $_)) })
if ($missingStaged.Count -gt 0) {
    Write-Host "ERROR: staging did not produce required files:" -ForegroundColor Red
    $missingStaged | ForEach-Object { Write-Host "  $_" }
    exit 1
}

# ------------------------------------------------------ 14. licence harvest
Write-Host "Harvesting third-party licences ..."
$licRoot = Join-Path $stageDir 'licenses'
New-Item -ItemType Directory -Force $licRoot | Out-Null
$sitePackages = Join-Path $buildVenv 'Lib\site-packages'
foreach ($distInfo in Get-ChildItem $sitePackages -Directory -Filter '*.dist-info') {
    $pkg = ($distInfo.Name -replace '\.dist-info$', '')
    $files = @(Get-ChildItem $distInfo.FullName -Recurse -File |
               Where-Object { $_.Name -match '(?i)^(LICEN[SC]E|COPYING|NOTICE)' })
    if ($files.Count -eq 0) { continue }
    $target = Join-Path $licRoot $pkg
    New-Item -ItemType Directory -Force $target | Out-Null
    $files | ForEach-Object { Copy-Item $_.FullName $target -Force }
}

# pystray is LGPLv3. Shipping its sources, together with this repo's public and
# reproducible build script, is what lets a user rebuild the bundle against a
# modified pystray - the relinking right the licence requires. Its modules live
# inside the PYZ archive in the exe, so there is nothing swappable on disk.
$pystraySrc = Join-Path $sitePackages 'pystray'
if (Test-Path $pystraySrc) {
    $dest = Join-Path $licRoot 'pystray\src'
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item (Join-Path $pystraySrc '*.py') $dest -Force
}

# ------------------------------------------------------- 15. BUILD-INFO.txt
$pyiVersion = (& $buildVenvPython -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
$fullPy = (& $buildVenvPython -c "import sys; print(sys.version.split()[0])").Trim()
$buildInfo = @"
WhisperPaste $appVersion - portable build
Built (UTC)      : $((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss'))
Git commit       : $gitCommit
Model            : $Model ($($modelEntry.repo_id) @ $($modelEntry.revision))
Python           : $fullPy
PyInstaller      : $pyiVersion
requirements-build.txt SHA-256 : $reqHash

Rebuild this artifact from source with:  scripts\build.ps1 -Model $Model
"@
Set-Content -Path (Join-Path $stageDir 'BUILD-INFO.txt') -Value $buildInfo -Encoding utf8

$sizeMb = (Get-ChildItem $stageDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Bundle: {0:N1} MB at {1}" -f $sizeMb, $stageDir) -ForegroundColor Green

# ------------------------------------------------------------- 16. the ZIP
if ($SkipZip) {
    Write-Host "Skipping the ZIP (-SkipZip)." -ForegroundColor Yellow
    exit 0
}

New-Item -ItemType Directory -Force $OutputDir | Out-Null
$zipPath = Join-Path $OutputDir "WhisperPaste-$appVersion-win64-$Model.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

# Not Compress-Archive: in PS 5.1 it is slow and memory-hungry on a ~700 MB
# tree and has historically hit a 2 GB ceiling.
Write-Host "Compressing (this takes a few minutes) ..."
Add-Type -AssemblyName System.IO.Compression.FileSystem
try {
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $stageDir, $zipPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $true)   # include the top-level folder, so it unzips to WhisperPaste\
} catch {
    Write-Host "ERROR: compression failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

$zipMb = (Get-Item $zipPath).Length / 1MB
$zipHash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLower()
Write-Host ""
Write-Host ("ZIP     : {0} ({1:N1} MB)" -f $zipPath, $zipMb) -ForegroundColor Green
Write-Host   "SHA-256 : $zipHash" -ForegroundColor Green
Write-Host ""
Write-Host "Publish that SHA-256 with the download - the README tells users to check it." -ForegroundColor Cyan
exit 0
