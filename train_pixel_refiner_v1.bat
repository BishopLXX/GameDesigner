@echo off
setlocal

cd /d "%~dp0"

set TRAIN_PY=D:\GameDesignerData\venvs\pixel-refiner-train\Scripts\python.exe
set MODEL_DIR=D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v1
set PYTHONPATH=%CD%\source

if not exist "%TRAIN_PY%" (
    echo [PixelRefiner] Training Python was not found:
    echo   %TRAIN_PY%
    echo.
    echo Open Pixel Refiner Console or recreate the training environment first.
    pause
    exit /b 1
)

echo [PixelRefiner] Dataset summary:
"%TRAIN_PY%" source\pixel_refiner_training_main.py summary
if errorlevel 1 (
    echo.
    echo Dataset summary failed.
    pause
    exit /b 1
)

echo.
echo [PixelRefiner] This will train and overwrite:
echo   %MODEL_DIR%
echo.
echo Close or restart the Pixel Refiner service after training so it reloads the new ONNX weights.
echo.
choice /C YN /M "Start v1 training now"
if errorlevel 2 (
    echo Training cancelled.
    pause
    exit /b 0
)

echo.
echo [PixelRefiner] Training started...
"%TRAIN_PY%" source\pixel_refiner_training_main.py train ^
  --output-dir "%MODEL_DIR%" ^
  --epochs 4 ^
  --steps-per-epoch 900 ^
  --batch-size 8 ^
  --patch-size 128 ^
  --device cuda ^
  --val-batches 32 ^
  --log-interval 50

if errorlevel 1 (
    echo.
    echo Training failed.
    pause
    exit /b 1
)

echo.
echo [PixelRefiner] Training complete. Running model smoke test...
"%TRAIN_PY%" source\pixel_refiner_training_main.py smoke-model --model-dir "%MODEL_DIR%"
if errorlevel 1 (
    echo.
    echo Smoke test failed.
    pause
    exit /b 1
)

echo.
echo [PixelRefiner] Done. Restart Pixel Refiner service before testing in GameDesigner.
pause
