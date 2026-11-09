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

echo 用法：build.bat [Release^|Debug]
echo Release 編譯可攜式程式，並打成 dist 裡每份 %PART_SIZE_MB% MB 的 7z 分割檔。
echo 只留下 .7z.001、.7z.002……。用 7-Zip 開啟第一份即可解壓。
echo 內容含程式、轉譜模型、找得到的分軌權重；有 cpp\build\cuda-ep 時含 CUDA。
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

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\pack-release.ps1" -RepoRoot "%~dp0." -PartSizeMB %PART_SIZE_MB%
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
