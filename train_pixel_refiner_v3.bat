@echo off
setlocal

cd /d "%~dp0"
set "PYTHONPATH=%CD%\source"
set "TRAIN_PY=D:\GameDesignerData\venvs\pixel-refiner-train\Scripts\python.exe"

if not exist "%TRAIN_PY%" (
  echo Training Python not found:
  echo   %TRAIN_PY%
  echo Create the training environment first, or use PixelRefinerConsole.exe to choose a training Python.
  pause
  exit /b 1
)

"%TRAIN_PY%" source\pixel_refiner_training_main.py train ^
  --model-id pixel-refiner-v3 ^
  --architecture pixel-tile-v3 ^
  --output-dir "D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v3" ^
  --epochs 4 ^
  --steps-per-epoch 900 ^
  --batch-size 4 ^
  --patch-size 64 ^
  --internal-scale 2 ^
  --tile-overlap 16 ^
  --block-consistency-weight 0.20 ^
  --features 64 ^
  --palette-levels 64 ^
  --pixel-constraint-weight 0.08 ^
  --val-batches 32 ^
  --log-interval 50 ^
  --device cuda

pause
