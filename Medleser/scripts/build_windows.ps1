<#
Bygger Medleser for Windows:
  1. kjører testene (kan hoppes over med -SkipTests)
  2. lager ikonfilen
  3. bygger programmappen dist\Medleser med PyInstaller
  4. kjører selvtest av den ferdige Medleser.exe
  5. lager veiledningene som PDF og installasjonsfilen dist\Medleser-Setup-<versjon>.exe med Inno Setup (hoppes over med -SkipInstaller)

Kjør fra prosjektmappen:
  powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#>
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Fant ikke .venv. Følg INSTALL_WINDOWS.md (avsnittet for utviklere) først." }

if (-not $SkipTests) {
    Write-Host "== Kjører tester"
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Testene feilet – bygget stoppes." }
}

Write-Host "== Lager ikon"
& $py scripts\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "Ikonet kunne ikke lages." }

Write-Host "== Bygger med PyInstaller"
# pywinrt har med en eldre msvcp140.dll som får onnxruntime til å krasje når PyInstaller
# undersøker pakkene (se medleser/runtime_fix.py). Den gis et annet navn mens bygget kjører.
$winrtRuntime = Join-Path $root ".venv\Lib\site-packages\winrt\msvcp140.dll"
$winrtRuntimeOff = "$winrtRuntime.disabled-during-build"
if (Test-Path $winrtRuntime) { Rename-Item $winrtRuntime $winrtRuntimeOff }
try {
    & $py -m PyInstaller packaging\medleser.spec --noconfirm --clean --distpath dist --workpath build
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller feilet." }
}
finally {
    if (Test-Path $winrtRuntimeOff) { Rename-Item $winrtRuntimeOff (Split-Path $winrtRuntime -Leaf) }
}

Write-Host "== Selvtest av ferdig program"
$result = Join-Path $root "dist\selftest.json"
Remove-Item $result -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath (Join-Path $root "dist\Medleser\Medleser.exe") -ArgumentList @("--selftest", "`"$result`"") -Wait -PassThru
if ($proc.ExitCode -ne 0 -or -not (Test-Path $result)) { throw "Selvtesten feilet (kode $($proc.ExitCode)). Se $result" }
Write-Host "Selvtest OK: $result"

if (-not $SkipInstaller) {
    $iscc = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw "Fant ikke Inno Setup 6 (ISCC.exe). Installer det, eller bruk -SkipInstaller." }
    Write-Host "== Lager veiledninger (PDF)"
    & $py scripts\make_guides.py
    if ($LASTEXITCODE -ne 0) { throw "Veiledningene kunne ikke lages." }
    Write-Host "== Lager installasjonsfil"
    & $iscc installer\medleser.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup feilet." }
    Get-ChildItem dist\Medleser-Setup-*.exe | ForEach-Object { Write-Host "Ferdig: $($_.FullName) ($([int]($_.Length / 1MB)) MB)" }
}
