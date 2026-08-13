<#
.SYNOPSIS
    Audits an installed WhisperPaste environment for known vulnerabilities.
.DESCRIPTION
    Audits .venv by default. The scanner lives separately in
    build\audit-venv, so it never changes the environment being checked or the
    portable app. Internet access is required for current advisory data.
.PARAMETER Venv
    Virtual environment to audit. Relative paths are resolved from the repo.
.EXAMPLE
    scripts\audit.ps1
.EXAMPLE
    scripts\audit.ps1 -Venv build\venv
#>
[CmdletBinding()]
param(
    [string]$Venv
)

$root = Split-Path -Parent $PSScriptRoot
if (-not $Venv) { $Venv = Join-Path $root '.venv' }
if (-not [IO.Path]::IsPathRooted($Venv)) { $Venv = Join-Path $root $Venv }
$Venv = [IO.Path]::GetFullPath($Venv)

$targetPython = Join-Path $Venv 'Scripts\python.exe'
$sitePackages = Join-Path $Venv 'Lib\site-packages'
if (-not (Test-Path $targetPython) -or -not (Test-Path $sitePackages)) {
    Write-Host "ERROR: No usable virtual environment at $Venv." -ForegroundColor Red
    Write-Host "Run scripts\install.ps1 first, or pass -Venv <path>."
    exit 1
}

$auditVersion = '2.10.1'
$auditVenv = Join-Path $root 'build\audit-venv'
$auditPython = Join-Path $auditVenv 'Scripts\python.exe'
$stamp = Join-Path $auditVenv '.pip-audit-version'
$auditReady = (Test-Path $auditPython) -and (Test-Path $stamp) -and
              ((Get-Content $stamp -Raw).Trim() -eq $auditVersion)

if (-not $auditReady) {
    if (Test-Path $auditVenv) { Remove-Item -Recurse -Force $auditVenv }
    New-Item -ItemType Directory -Force (Split-Path -Parent $auditVenv) | Out-Null
    Write-Host "Creating isolated dependency-audit environment ..."
    $global:LASTEXITCODE = $null
    try { & $targetPython -I -m venv $auditVenv } catch { }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Could not create the dependency-audit environment." -ForegroundColor Red
        exit 1
    }

    Write-Host "Installing pip-audit $auditVersion ..."
    $global:LASTEXITCODE = $null
    try {
        & $auditPython -I -m pip install --only-binary :all: --isolated `
            "pip-audit==$auditVersion"
    } catch { }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Could not install pip-audit." -ForegroundColor Red
        exit 1
    }
    Set-Content -Path $stamp -Value $auditVersion -Encoding ascii
}

Write-Host "Auditing installed dependencies in $Venv ..." -ForegroundColor Cyan
$global:LASTEXITCODE = $null
try {
    & $auditPython -I -m pip_audit --strict --progress-spinner off `
        --path $sitePackages
} catch { }
$auditCode = $LASTEXITCODE
if ($null -eq $auditCode -or $auditCode -ne 0) {
    Write-Host "ERROR: Dependency audit failed; refusing to continue." -ForegroundColor Red
    if ($null -eq $auditCode) { exit 1 }
}
exit $auditCode
