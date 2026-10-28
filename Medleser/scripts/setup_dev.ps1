<#
Setter opp utviklingsmiljøet for Medleser på en ny maskin (etter git clone):
  1. finner Python 3.13
  2. lager .venv og installerer pakkene (requirements-dev.txt)
  3. kjører testene

Kjør fra mappen Medleser:
  powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1
#>
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Fant ikke Python 3.13. Installer det først (per bruker, uten administrator):"
    Write-Host "  winget install -e --id Python.Python.3.13 --scope user"
    throw "Python 3.13 mangler."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "== Lager .venv"
    & $python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Kunne ikke lage .venv." }
}
$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "== Installerer pakker"
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Pakkeinstallasjonen feilet." }

if (-not $SkipTests) {
    Write-Host "== Kjører tester"
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Noen tester feilet – se over." }
}

Write-Host ""
Write-Host "Ferdig. Start appen med:  .venv\Scripts\python.exe -m medleser"
Write-Host "Last ned en norsk stemme i appen under «Stemmer og KI-tjenester»."
Write-Host "Bygg installasjonsfil (krever Inno Setup 6):  powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1"
