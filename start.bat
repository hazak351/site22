@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AdFlow Studio Auto

echo.
echo  ==========================================
echo    AdFlow Studio Auto
echo  ==========================================
echo.

rem --- Python -------------------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :erro_python

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Criando ambiente virtual .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto :erro_venv
)
set "VPY=%~dp0.venv\Scripts\python.exe"

rem --- Dependencias: reinstala so quando requirements.txt muda ------------
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
  echo [2/3] Instalando dependencias, pode levar alguns minutos ...
  "%VPY%" -m pip install --upgrade pip >nul
  "%VPY%" -m pip install --prefer-binary -r requirements.txt
  if errorlevel 1 goto :erro_pip
  copy /y requirements.txt ".venv\requirements.installed" >nul
) else (
  echo [2/3] Dependencias ja instaladas.
)

rem --- FFmpeg ---------------------------------------------------------------
if exist "%~dp0tools\ffmpeg\bin\ffmpeg.exe" set "PATH=%~dp0tools\ffmpeg\bin;%PATH%"
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo [AVISO] FFmpeg nao encontrado. Sem ele o video final nao e montado.
  echo         Instale com:  winget install Gyan.FFmpeg
  echo         ou copie ffmpeg.exe e ffprobe.exe para tools\ffmpeg\bin
  echo.
)

echo [3/3] Verificando ambiente ...
"%VPY%" -m adflow check
echo.
echo Abrindo http://127.0.0.1:8765  -  feche esta janela para parar o AdFlow.
"%VPY%" -m adflow serve
if errorlevel 1 pause
exit /b 0

:erro_python
echo [ERRO] Python nao encontrado. Instale em https://www.python.org/downloads/
echo        e marque a opcao "Add python.exe to PATH".
pause
exit /b 1

:erro_venv
echo [ERRO] Nao foi possivel criar o ambiente virtual .venv
pause
exit /b 1

:erro_pip
echo [ERRO] Falha ao instalar dependencias. Veja a mensagem acima.
echo        Se algum pacote tentou compilar, use Python 3.12 ou 3.13.
del ".venv\requirements.installed" >nul 2>nul
pause
exit /b 1
