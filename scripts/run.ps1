<#
.SYNOPSIS
    Launch WhisperPaste from the project's virtual environment.
.DESCRIPTION
    Runs "python -m whisper_paste", forwarding any extra arguments to the app.
    Pass -NoConsole to launch detached (no console window) so it lives only in
    the system tray.
.EXAMPLE
    scripts\run.ps1
.EXAMPLE
    scripts\run.ps1 --model small --lang en
.EXAMPLE
    scripts\run.ps1 -NoConsole
#>
[CmdletBinding()]
param(
    [switch]$NoConsole,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArgs
)

# NOTE: deliberately not setting $ErrorActionPreference = 'Stop'. In Windows
# PowerShell 5.1 that turns redirected native stderr (e.g. Python warnings,
# argparse usage text) into terminating errors. Exit codes are checked
# explicitly instead.

# Repo root is the parent of the scripts/ folder - independent of the caller's CWD.
$root = Split-Path -Parent $PSScriptRoot

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: No virtual environment found." -ForegroundColor Red
    Write-Host "Run scripts\install.ps1 first."
    exit 1
}

if (-not $AppArgs) {
    $AppArgs = @()
}

if ($NoConsole) {
    $venvPythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path $venvPythonw)) {
        Write-Host "ERROR: $venvPythonw is missing." -ForegroundColor Red
        Write-Host "Run scripts\install.ps1 first."
        exit 1
    }
    $startArgs = @('-m', 'whisper_paste') + $AppArgs
    Start-Process -FilePath $venvPythonw -ArgumentList $startArgs -WorkingDirectory $root | Out-Null
    Write-Host "WhisperPaste started in the system tray; quit via the tray icon." -ForegroundColor Green
    exit 0
}

# Run from the repo root so "python -m whisper_paste" resolves the package
# regardless of the caller's CWD (e.g. double-clicking scripts\run.bat).
Push-Location $root
try {
    & $venvPython -m whisper_paste @AppArgs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
