@echo off
REM Start ComfyUI as the image backend for the pipeline.
REM Leave this running in its own window; step 3 talks to it over HTTP.
REM
REM --reserve-vram is the important one. This is a 16 GB card driving a desktop
REM that already holds ~2.5 GB in browsers and Electron apps. Left to itself
REM ComfyUI budgets as if the whole card were free and OOMs partway through a
REM long batch; holding 1.5 GB back costs nothing and never falls over.
REM Default (normal) VRAM mode is right here -- no flag needed.

cd /d "%~dp0.."
set PYTHONUNBUFFERED=1

REM The model-paths config lives in this repo rather than inside comfy\, which
REM is a clone and is gitignored. Passing it explicitly means re-cloning
REM ComfyUI never costs a 17 GB model redownload.

.venv-comfy\Scripts\python.exe comfy\main.py ^
  --listen 127.0.0.1 ^
  --port 8188 ^
  --reserve-vram 1.5 ^
  --extra-model-paths-config config\extra_model_paths.yaml ^
  --preview-method none ^
  --disable-auto-launch

pause
