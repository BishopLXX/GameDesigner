@echo off
setlocal

cd /d "%~dp0"
set PYTHONPATH=%CD%\source
py -3 source\pixel_refiner_service_window.py --model-dir D:\GameDesignerData\pixel_refiner\models\pixel-refiner-v2
