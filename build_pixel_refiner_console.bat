@echo off
setlocal

cd /d "%~dp0"

tasklist /FI "IMAGENAME eq PixelRefinerConsole.exe" 2>nul | find /I "PixelRefinerConsole.exe" >nul
if not errorlevel 1 (
    echo.
    echo PixelRefinerConsole.exe is currently running. Please close it before building release.
    pause
    exit /b 1
)

echo [PixelRefinerConsole] Installing GUI and service dependencies...
py -3.13 -m pip install --upgrade -r source\requirements.txt
py -3.13 -m pip install --upgrade -r source\pixel_refiner_requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency install failed. Please make sure Python 3.13 is installed and py -3.13 works.
    pause
    exit /b 1
)

echo.
echo [PixelRefinerConsole] Building release\PixelRefinerConsole.exe...
py -3.13 -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name PixelRefinerConsole ^
  --icon icon.ico ^
  --hidden-import pixel_refiner_service_main ^
  --hidden-import pixel_refiner_service.server ^
  --hidden-import pixel_refiner_service.backend ^
  --hidden-import pixel_refiner_training_main ^
  --hidden-import pixel_refiner_test_runner ^
  --hidden-import gamedesigner.pixel_refiner ^
  --hidden-import gamedesigner.pixel_refiner_dataset ^
  --hidden-import gamedesigner.pixel_refiner_dataset_eval ^
  --hidden-import gamedesigner.pixel_refiner_eval_suite ^
  --hidden-import gamedesigner.pixel_refiner_pair_generation ^
  --hidden-import gamedesigner.pixel_refiner_ai_pseudo ^
  --distpath release ^
  --workpath build_pixel_refiner_console ^
  source\pixel_refiner_service_window.py

if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Build complete:
echo   %CD%\release\PixelRefinerConsole.exe
pause
