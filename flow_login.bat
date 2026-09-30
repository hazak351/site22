@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Rode start.bat primeiro para instalar o AdFlow.
  pause
  exit /b 1
)
echo Abrindo o navegador de automacao do AdFlow no Google Flow ...
echo Faca login na sua conta Google nessa janela. O AdFlow nao ve nem salva sua senha.
".venv\Scripts\python.exe" -m adflow login
pause
