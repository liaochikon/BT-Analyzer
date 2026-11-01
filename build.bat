@echo off
setlocal
cd /d "%~dp0"

set "CONFIG=Release"
if not "%~1"=="" set "CONFIG=%~1"

where cmake >nul 2>&1
if errorlevel 1 (
  echo 找不到 cmake。
  goto :finish
)

cmake -S cpp -B cpp\build -G "Visual Studio 17 2022" -A x64
if errorlevel 1 goto :fail

cmake --build cpp\build --config %CONFIG%
if errorlevel 1 goto :fail

echo.
echo 編譯完成：cpp\build\%CONFIG%\bt-app.exe
echo 發布內容：onnxruntime.dll、models、fonts 已放在 cpp\build\%CONFIG%
goto :finish

:fail
echo.
echo 編譯失敗。
set "EXITCODE=1"

:finish
if not defined EXITCODE set "EXITCODE=0"
echo %CMDCMDLINE% | find /i "%~f0" >nul
if not errorlevel 1 pause
exit /b %EXITCODE%
