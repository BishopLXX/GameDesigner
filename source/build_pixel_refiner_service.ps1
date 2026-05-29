$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ReleaseDir = Join-Path $ProjectRoot "release"
$IconIcoPath = Join-Path $ProjectRoot "icon.ico"

py -3.13 -m pip install --upgrade -r (Join-Path $PSScriptRoot "pixel_refiner_requirements.txt")

if (Get-Process -Name "PixelRefiner" -ErrorAction SilentlyContinue) {
    throw "PixelRefiner.exe started while building. Please close it before building release."
}

py -3.13 -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --console `
  --name PixelRefiner `
  --icon $IconIcoPath `
  --distpath $ReleaseDir `
  --workpath (Join-Path $ProjectRoot "build_pixel_refiner") `
  (Join-Path $PSScriptRoot "pixel_refiner_service_main.py")

Write-Host "Pixel Refiner service build complete：" (Join-Path $ReleaseDir "PixelRefiner.exe")
