@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if /I "%~1"=="Debug" (
  set "CONFIG=Debug"
  set "DEV_TOOLS=ON"
  set "TARGET="
  goto :ready
)
if /I "%~1"=="Release" (
  set "CONFIG=Release"
  set "DEV_TOOLS=OFF"
  set "TARGET=bt-app"
  goto :ready
)

echo Usage: build-cpp.bat Debug^|Release
echo Or run build-debug.bat / build-release.bat.
echo Builds the C++ project only; does not package. Use build.bat for a full release.
echo Resources: export-onnx.bat -^> models\, export-icons.bat -^> assets\icons\, fetch-cuda.bat -^> cuda-ep\ ^(copied next to the exe on build^).
set "EXITCODE=1"
goto :finish

:ready
call :find_cmake
if errorlevel 1 goto :fail

call :configure
if errorlevel 1 goto :fail

echo.
if defined TARGET (
  echo Building %CONFIG% ^(%TARGET%^)...
  "%CMAKE%" --build cpp\build --config %CONFIG% --target %TARGET%
) else (
  echo Building %CONFIG% ^(all targets^)...
  "%CMAKE%" --build cpp\build --config %CONFIG%
)
if errorlevel 1 goto :fail

echo.
if /I "%CONFIG%"=="Debug" (
  echo Debug build ready: cpp\build\Debug\bt-app.exe
  echo Same folder also has bt-separate.exe and bt_stem_tests.exe.
) else (
  echo Release build ready: cpp\build\Release\bt-app.exe
  echo This script only builds; it does not package. Use build.bat for a full release.
)
set "EXITCODE=0"
goto :finish

:configure
if exist "cpp\build\CMakeCache.txt" (
  echo Reusing existing CMake build dir: cpp\build
  "%CMAKE%" -S cpp -B cpp\build -DBT_DEV_TOOLS=%DEV_TOOLS%
  exit /b %ERRORLEVEL%
)
call :pick_generator
if errorlevel 1 exit /b 1
echo Configuring: -G "%GEN%" -A x64 -DBT_DEV_TOOLS=%DEV_TOOLS%
"%CMAKE%" -S cpp -B cpp\build -G "%GEN%" -A x64 -DBT_DEV_TOOLS=%DEV_TOOLS%
exit /b %ERRORLEVEL%

:find_cmake
set "CMAKE="
where cmake >nul 2>&1
if not errorlevel 1 (
  set "CMAKE=cmake"
  exit /b 0
)
if exist "%ProgramFiles%\CMake\bin\cmake.exe" (
  set "CMAKE=%ProgramFiles%\CMake\bin\cmake.exe"
  exit /b 0
)
if exist "%ProgramFiles(x86)%\CMake\bin\cmake.exe" (
  set "CMAKE=%ProgramFiles(x86)%\CMake\bin\cmake.exe"
  exit /b 0
)
echo cmake not found. Install CMake and add it to PATH, or install under "%ProgramFiles%\CMake".
exit /b 1

:pick_generator
rem Prefer VS 2026 when present; build.bat still hardcodes 2022.
if exist "%ProgramFiles%\Microsoft Visual Studio\18\Community\VC\Tools\MSVC" (
  set "GEN=Visual Studio 18 2026"
  exit /b 0
)
if exist "%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC" (
  set "GEN=Visual Studio 17 2022"
  exit /b 0
)
echo Visual Studio C++ tools not found ^(need VS 2026 or VS 2022^).
exit /b 1

:fail
echo.
echo Failed.
set "EXITCODE=1"

:finish
if not defined EXITCODE set "EXITCODE=0"
echo %CMDCMDLINE% | findstr /i "build-debug.bat build-release.bat build-cpp.bat" >nul
if not errorlevel 1 pause
exit /b %EXITCODE%
