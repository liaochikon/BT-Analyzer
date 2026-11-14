@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem export-icons.bat
rem Renders monochrome UI icons into assets\icons\atlas.png + atlas.json
rem (and per-icon PNGs). Requires Python 3 with Pillow.
rem Build copies assets\icons\ next to the exe via copy_assets.cmake.

set "VENV=%~dp0.venv"
set "PY="
set "EXITCODE=0"

if exist "%VENV%\Scripts\python.exe" (
  set "PY=%VENV%\Scripts\python.exe"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Python not found. Install Python 3 or create .venv first.
    set "EXITCODE=1"
    goto :finish
  )
  set "PY=python"
)

echo Using: %PY%
"%PY%" -c "import PIL" >nul 2>&1
if errorlevel 1 (
  echo Installing Pillow...
  "%PY%" -m pip install --disable-pip-version-check pillow
  if errorlevel 1 (
    echo Failed to install Pillow.
    set "EXITCODE=1"
    goto :finish
  )
)

echo Rendering icons...
"%PY%" tools\render_icons.py
if errorlevel 1 (
  echo Icon export failed.
  set "EXITCODE=1"
  goto :finish
)

echo.
echo Icons ready: assets\icons\atlas.png
echo             assets\icons\atlas.json
echo Build copies them next to bt-app.exe.
set "EXITCODE=0"

:finish
exit /b %EXITCODE%
