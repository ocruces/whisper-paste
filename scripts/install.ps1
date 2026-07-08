<#
.SYNOPSIS
    One-time setup for WhisperPaste: creates a .venv and installs dependencies.
.DESCRIPTION
    Locates a suitable Python (>= 3.10), creates a virtual environment at the
    repo root (if missing), and installs the runtime requirements. Pass -Dev to
    also install the development/test requirements.
.EXAMPLE
    scripts\install.ps1
.EXAMPLE
    scripts\install.ps1 -Dev
#>
[CmdletBinding()]
param(
    [switch]$Dev
)

# NOTE: deliberately not setting $ErrorActionPreference = 'Stop'. In Windows
# PowerShell 5.1 that turns redirected native stderr (e.g. pip WARNING lines)
# into terminating errors. $LASTEXITCODE is checked explicitly after every
# native call instead.

# Repo root is the parent of the scripts/ folder - independent of the caller's CWD.
$root = Split-Path -Parent $PSScriptRoot

Write-Host "WhisperPaste setup" -ForegroundColor Cyan
Write-Host "Repo root: $root"

function Test-PythonOk {
    # Runs a candidate python and returns $true if it is >= 3.10.
    param(
        [string]$Exe,
        [string[]]$PreArgs = @()
    )
    try {
        & $Exe @PreArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    } catch {
        return $false
    }
    return ($LASTEXITCODE -eq 0)
}

# Locate Python: prefer the "py -3" launcher, then "python" on PATH.
$pyExe = $null
$pyPreArgs = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
    if (Test-PythonOk -Exe 'py' -PreArgs @('-3')) {
        $pyExe = 'py'
        $pyPreArgs = @('-3')
    }
}
if (-not $pyExe) {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        if (Test-PythonOk -Exe 'python') {
            $pyExe = 'python'
            $pyPreArgs = @()
        }
    }
}

if (-not $pyExe) {
    Write-Host ""
    Write-Host "ERROR: No Python 3.10+ found." -ForegroundColor Red
    Write-Host "Install Python 3.10 or newer from https://www.python.org/downloads/"
    Write-Host "(During install, tick 'Add python.exe to PATH'.)"
    exit 1
}

$reportedVersion = & $pyExe @pyPreArgs -c "import sys; print(sys.version.split()[0])"
Write-Host "Using Python $reportedVersion ($pyExe $($pyPreArgs -join ' '))" -ForegroundColor Green

# Create the virtual environment (idempotent).
$venv = Join-Path $root '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'

if (Test-Path $venvPython) {
    Write-Host ".venv already exists - reusing it." -ForegroundColor Yellow
} else {
    Write-Host "Creating virtual environment at $venv ..."
    & $pyExe @pyPreArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to create the virtual environment." -ForegroundColor Red
        exit 1
    }
}

if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: Expected $venvPython but it is missing." -ForegroundColor Red
    exit 1
}

# Upgrade pip so wheels resolve cleanly, then install runtime deps.
Write-Host "Upgrading pip ..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to upgrade pip." -ForegroundColor Red
    exit 1
}

Write-Host "Installing runtime dependencies (requirements.txt) ..."
& $venvPython -m pip install -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install runtime dependencies." -ForegroundColor Red
    exit 1
}

if ($Dev) {
    Write-Host "Installing development dependencies (requirements-dev.txt) ..."
    & $venvPython -m pip install -r (Join-Path $root 'requirements-dev.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to install development dependencies." -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "Done. Start WhisperPaste with:  scripts\run.ps1" -ForegroundColor Green
exit 0
