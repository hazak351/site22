@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Rode start.bat primeiro para instalar o AdFlow.
  pause
  exit /b 1
)
if exist "%~dp0tools\ffmpeg\bin\ffmpeg.exe" set "PATH=%~dp0tools\ffmpeg\bin;%PATH%"
".venv\Scripts\python.exe" -m pip install --prefer-binary -r requirements-dev.txt
".venv\Scripts\python.exe" -m pytest -q
pause
