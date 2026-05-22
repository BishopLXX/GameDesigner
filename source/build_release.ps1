$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ReleaseDir = Join-Path $ProjectRoot "release"
$IconPath = Join-Path $ProjectRoot "icon.png"

py -3.13 -m pip install --upgrade -r (Join-Path $PSScriptRoot "requirements.txt")
if (Get-Process -Name "GameDesigner" -ErrorAction SilentlyContinue) {
    throw "GameDesigner.exe started while building. Please close it before building release."
}
py -3.13 -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name GameDesigner `
  --add-data "$IconPath;." `
  --distpath $ReleaseDir `
  --workpath (Join-Path $ProjectRoot "build") `
  (Join-Path $PSScriptRoot "main.py")

Write-Host "打包完成：" (Join-Path $ReleaseDir "GameDesigner.exe")
