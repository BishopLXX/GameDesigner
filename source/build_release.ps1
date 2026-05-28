$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ReleaseDir = Join-Path $ProjectRoot "release"
$IconPngPath = Join-Path $ProjectRoot "icon.png"
$IconIcoPath = Join-Path $ProjectRoot "icon.ico"

py -3.13 -m pip install --upgrade -r (Join-Path $PSScriptRoot "requirements.txt")
py -3.13 (Join-Path $PSScriptRoot "make_icon.py") $IconPngPath $IconIcoPath
$PySideTranslations = py -3.13 -c "from pathlib import Path; import PySide6; print(Path(PySide6.__file__).resolve().parent / 'translations')"
if (-not (Test-Path (Join-Path $PySideTranslations "qtbase_zh_CN.qm"))) {
    throw "Qt Chinese translation files were not found."
}
if (-not (Test-Path (Join-Path $PySideTranslations "qt_zh_CN.qm"))) {
    throw "Qt Chinese translation files were not found."
}
if (Get-Process -Name "GameDesigner" -ErrorAction SilentlyContinue) {
    throw "GameDesigner.exe started while building. Please close it before building release."
}
py -3.13 -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --splash $IconPngPath `
  --windowed `
  --name GameDesigner `
  --icon $IconIcoPath `
  --add-data "$IconPngPath;." `
  --add-data "$(Join-Path $PySideTranslations "qtbase_zh_CN.qm");PySide6\translations" `
  --add-data "$(Join-Path $PySideTranslations "qt_zh_CN.qm");PySide6\translations" `
  --distpath $ReleaseDir `
  --workpath (Join-Path $ProjectRoot "build") `
  (Join-Path $PSScriptRoot "main.py")

$GameDesignerExe = Join-Path $ReleaseDir "GameDesigner.exe"
py -3.13 -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --console `
  --name GameDesigner-Setup `
  --icon $IconIcoPath `
  --add-binary "$GameDesignerExe;." `
  --distpath $ReleaseDir `
  --workpath (Join-Path $ProjectRoot "build_setup") `
  (Join-Path $PSScriptRoot "setup_installer.py")

Write-Host "主程序打包完成：" $GameDesignerExe
Write-Host "安装器打包完成：" (Join-Path $ReleaseDir "GameDesigner-Setup.exe")
