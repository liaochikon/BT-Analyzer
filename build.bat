@echo off
setlocal
cd /d "%~dp0"

set "CONFIG=Release"
set "DEV_TOOLS=OFF"
set "PART_SIZE_MB=200"
if "%~1"=="" goto :configured
if /I "%~1"=="Release" goto :configured
if /I "%~1"=="Debug" (
  set "CONFIG=Debug"
  set "DEV_TOOLS=ON"
  goto :configured
)

echo Usage: build.bat [Release^|Debug]
echo Release builds the portable app and packs split 7z archives under dist\,
echo each %PART_SIZE_MB% MB. Only .7z.001, .7z.002, ... remain; open the first part with 7-Zip.
echo Contents: app, transcription model, any stem weights found; CUDA when cuda-ep\ exists ^(fetch-cuda.bat^).
echo Debug also builds CLI tools and tests; it does not package.
set "EXITCODE=1"
goto :finish

:configured
where cmake >nul 2>&1
if errorlevel 1 (
  echo cmake not found.
  set "EXITCODE=1"
  goto :finish
)

cmake -S cpp -B cpp\build -G "Visual Studio 17 2022" -A x64 -DBT_DEV_TOOLS=%DEV_TOOLS%
if errorlevel 1 goto :fail

if /I "%CONFIG%"=="Release" (
  cmake --build cpp\build --config Release --target bt-app
) else (
  cmake --build cpp\build --config Debug
)
if errorlevel 1 goto :fail

if /I not "%CONFIG%"=="Release" (
  echo.
  echo Debug build ready: cpp\build\Debug\bt-app.exe
  echo CLI tools and tests are in the same folder. No package was created.
  goto :finish
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\pack-release.ps1" -RepoRoot "%~dp0." -PartSizeMB %PART_SIZE_MB%
if errorlevel 1 goto :fail
goto :finish

:fail
echo.
echo Failed.
set "EXITCODE=1"

:finish
if not defined EXITCODE set "EXITCODE=0"
echo %CMDCMDLINE% | find /i "%~f0" >nul
if not errorlevel 1 pause
exit /b %EXITCODE%
