@echo off
setlocal

cd /d "%~dp0"

set TRAIN_PY=D:\GameDesignerData\venvs\pixel-refiner-train\Scripts\python.exe
set MODEL_DIR=D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v2
set PYTHONPATH=%CD%\source

if not exist "%TRAIN_PY%" (
    echo [PixelRefinerV2] Training Python was not found:
    echo   %TRAIN_PY%
    pause
    exit /b 1
)

echo [PixelRefinerV2] Dataset summary:
"%TRAIN_PY%" source\pixel_refiner_training_main.py summary
if errorlevel 1 (
    echo.
    echo Dataset summary failed.
    pause
    exit /b 1
)

echo.
echo [PixelRefinerV2] This will train the medium U-Net/NAFNet refiner and write:
echo   %MODEL_DIR%
echo.
echo It uses pixel-art constraints: palette clamp, alpha clamp, and v2 cleanup manifest flags.
echo Restart Pixel Refiner service after training so it reloads the new weights.
echo.
choice /C YN /M "Start V2 training now"
if errorlevel 2 (
    echo Training cancelled.
    pause
    exit /b 0
)

echo.
echo [PixelRefinerV2] Training started...
"%TRAIN_PY%" source\pixel_refiner_training_main.py train ^
  --model-id pixel-refiner-v2 ^
  --architecture unet-naf-v2 ^
  --output-dir "%MODEL_DIR%" ^
  --epochs 4 ^
  --steps-per-epoch 700 ^
  --batch-size 4 ^
  --patch-size 256 ^
  --features 64 ^
  --device cuda ^
  --val-batches 24 ^
  --palette-levels 64 ^
  --alpha-threshold 128 ^
  --pixel-constraint-weight 0.08 ^
  --log-interval 50

if errorlevel 1 (
    echo.
    echo V2 training failed.
    pause
    exit /b 1
)

echo.
echo [PixelRefinerV2] Training complete. Running model smoke test...
"%TRAIN_PY%" source\pixel_refiner_training_main.py smoke-model --model-id pixel-refiner-v2 --model-dir "%MODEL_DIR%"
if errorlevel 1 (
    echo.
    echo Smoke test failed.
    pause
    exit /b 1
)

echo.
echo [PixelRefinerV2] Done. Restart Pixel Refiner service before testing in GameDesigner.
pause
