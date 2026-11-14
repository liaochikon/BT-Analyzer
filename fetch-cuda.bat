@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem fetch-cuda.bat
rem Builds repo-root cuda-ep\ for portable CUDA:
rem   1) ONNX Runtime 1.31.0 GPU package (CUDA EP plugin)
rem   2) NVIDIA CUDA/cuDNN runtime DLLs via pip wheels (cublasLt, cudnn, ...)
rem Default: CUDA 12. Override: fetch-cuda.bat --cuda 13
rem Force re-download: fetch-cuda.bat --force
rem Requires network + pip. Output is large (~1 GB+).

set "PY="
set "CUDA=12"
set "FORCE="
set "EXITCODE=0"

:parse
if "%~1"=="" goto :parsed
if /I "%~1"=="--cuda" (
  if "%~2"=="" (
    echo Missing value after --cuda. Use 12 or 13.
    set "EXITCODE=1"
    goto :finish
  )
  set "CUDA=%~2"
  shift
  shift
  goto :parse
)
if /I "%~1"=="--force" (
  set "FORCE=--force"
  shift
  goto :parse
)
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="--help" goto :usage
echo Unknown argument: %~1
goto :usage

:usage
echo Usage: fetch-cuda.bat [--cuda 12^|13] [--force]
echo.
echo Writes cuda-ep\ next to this script. build-debug / build-release / build.bat
echo copy those DLLs next to bt-app.exe when present.
set "EXITCODE=1"
goto :finish

:parsed
if not "%CUDA%"=="12" if not "%CUDA%"=="13" (
  echo --cuda must be 12 or 13, got %CUDA%.
  set "EXITCODE=1"
  goto :finish
)

call :find_python
if errorlevel 1 goto :fail

echo Using Python: %PY%
echo Fetching ORT 1.31.0 CUDA %CUDA% -^> cuda-ep\
"%PY%" "%~dp0tools\fetch_cuda_ep.py" --cuda %CUDA% %FORCE%
if errorlevel 1 goto :fail

echo.
echo Ready: cuda-ep\
echo Next: build-debug.bat / build-release.bat  ^(or build.bat Release to pack^)
goto :finish

:find_python
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
  for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PY (
      for /f "delims=" %%P in ('py -%%V -c "import sys; print(sys.executable)" 2^>nul') do (
        if exist "%%P" set "PY=%%P"
      )
    )
  )
)
if defined PY exit /b 0
where python >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%P in ('where python') do (
    if not defined PY set "PY=%%P"
  )
)
if defined PY exit /b 0
echo Python not found. Install it and ensure py or python is on PATH.
exit /b 1

:fail
echo.
echo Failed.
set "EXITCODE=1"

:finish
if not defined EXITCODE set "EXITCODE=0"
echo %CMDCMDLINE% | find /i "%~f0" >nul
if not errorlevel 1 pause
exit /b %EXITCODE%
