@echo off
setlocal

cd /d "%~dp0"

tasklist /FI "IMAGENAME eq GameDesigner.exe" 2>nul | find /I "GameDesigner.exe" >nul
if not errorlevel 1 (
    echo.
    echo GameDesigner.exe is currently running. Please close it before building release.
    pause
    exit /b 1
)

echo [GameDesigner] Installing runtime dependencies...
py -3.13 -m pip install --upgrade -r source\requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency install failed. Please make sure Python 3.13 is installed and py -3.13 works.
    pause
    exit /b 1
)

echo [GameDesigner] Preparing Windows icon...
py -3.13 source\make_icon.py icon.png icon.ico
if errorlevel 1 (
    echo.
    echo Icon generation failed. Please make sure icon.png exists and is readable.
    pause
    exit /b 1
)

tasklist /FI "IMAGENAME eq GameDesigner.exe" 2>nul | find /I "GameDesigner.exe" >nul
if not errorlevel 1 (
    echo.
    echo GameDesigner.exe started while building. Please close it before building release.
    pause
    exit /b 1
)

echo.
echo [GameDesigner] Building release\GameDesigner.exe...
py -3.13 -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name GameDesigner ^
  --icon icon.ico ^
  --add-data "icon.png;." ^
  --distpath release ^
  --workpath build ^
  source\main.py

if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Build complete: %CD%\release\GameDesigner.exe
pause
