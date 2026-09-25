@echo off
rem Jarvis-Max: the only file you need to click.
rem First start sets everything up by itself (a few minutes). Later starts take seconds.
setlocal
title Jarvis-Max
cd /d "%~dp0app"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set HF_HUB_DISABLE_TELEMETRY=1

echo.
echo   ==============================================
echo        J . A . R . V . I . S .    starting up
echo   ==============================================
echo.

rem --- 1. uv: the small tool that sets up Jarvis's Python and parts ---
set "UV="
where uv >nul 2>nul && set "UV=uv"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined UV (
  echo   Installing uv, a small setup tool. One time only.
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
)
if not defined UV goto no_uv

rem --- 2. Jarvis's parts (first start downloads them) ---
if not exist ".venv\" (
  echo   FIRST START: downloading Jarvis's parts, about 1 GB in all.
  echo   This takes a few minutes. Later starts take seconds.
  echo.
)
"%UV%" sync
if errorlevel 1 goto sync_failed

rem --- 3. Claude sign-in: asks only the first time ---
"%UV%" run --no-sync python signin.py

rem --- 4. Start Jarvis. Its window opens by itself. ---
echo.
echo   Starting Jarvis...
"%UV%" run --no-sync python server.py
if errorlevel 1 goto crashed
goto end

:no_uv
echo.
echo   Couldn't install uv (no internet?). Connect to the internet and click
echo   Start-Jarvis-Max.bat again.
pause
goto end

:sync_failed
echo.
echo   Downloading Jarvis's parts failed. Check the internet connection and
echo   click Start-Jarvis-Max.bat again. It carries on where it stopped.
pause
goto end

:crashed
echo.
echo   Jarvis stopped with an error. The message is above, and also in
echo   logs\jarvis.log inside the Jarvis-Max folder.
pause

:end
endlocal
