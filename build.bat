@echo off
setlocal
cd /d "%~dp0"

set "CONFIG=Release"
set "DEV_TOOLS=OFF"
if "%~1"=="" goto :configured
if /I "%~1"=="Release" goto :configured
if /I "%~1"=="Debug" (
  set "CONFIG=Debug"
  set "DEV_TOOLS=ON"
  goto :configured
)

echo 用法：build.bat [Release^|Debug]
echo Release 編譯玩音樂用的程式，並打成 dist 裡的壓縮檔。
echo Debug 另外編譯命令列工具和測試，不打包。
set "EXITCODE=1"
goto :finish

:configured
where cmake >nul 2>&1
if errorlevel 1 (
  echo 找不到 cmake。
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
  echo Debug 編譯完成：cpp\build\Debug\bt-app.exe
  echo 命令列工具和測試在同一個資料夾。這次沒有打包。
  goto :finish
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\pack-release.ps1" -RepoRoot "%~dp0."
if errorlevel 1 goto :fail
goto :finish

:fail
echo.
echo 失敗。
set "EXITCODE=1"

:finish
if not defined EXITCODE set "EXITCODE=0"
echo %CMDCMDLINE% | find /i "%~f0" >nul
if not errorlevel 1 pause
exit /b %EXITCODE%
