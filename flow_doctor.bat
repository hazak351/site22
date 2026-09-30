@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Rode start.bat primeiro para instalar o AdFlow.
  pause
  exit /b 1
)
if exist "%~dp0tools\ffmpeg\bin\ffmpeg.exe" set "PATH=%~dp0tools\ffmpeg\bin;%PATH%"
echo Flow Doctor: testa o Google Flow REAL sem gerar nada e sem gastar creditos.
".venv\Scripts\python.exe" -m adflow doctor
pause
