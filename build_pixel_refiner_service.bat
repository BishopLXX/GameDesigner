@echo off
setlocal

cd /d "%~dp0"

tasklist /FI "IMAGENAME eq PixelRefiner.exe" 2>nul | find /I "PixelRefiner.exe" >nul
if not errorlevel 1 (
    echo.
    echo PixelRefiner.exe is currently running. Please close it before building release.
    pause
    exit /b 1
)

echo [PixelRefiner] Installing service dependencies...
py -3.13 -m pip install --upgrade -r source\pixel_refiner_requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency install failed. Please make sure Python 3.13 is installed and py -3.13 works.
    pause
    exit /b 1
)

echo.
echo [PixelRefiner] Building release\PixelRefiner.exe...
py -3.13 -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --console ^
  --name PixelRefiner ^
  --icon icon.ico ^
  --distpath release ^
  --workpath build_pixel_refiner ^
  source\pixel_refiner_service_main.py

if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Build complete:
echo   %CD%\release\PixelRefiner.exe
pause
