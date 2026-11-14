@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

rem export-onnx.bat
rem Requires Python 3.11+ (demucs-onnx export minimum).
rem Creates .venv, installs deps, downloads missing weights, and writes all ONNX under models\:
rem   nmp.onnx, htdemucs_6s.onnx, bs_roformer_sw.onnx, bs_roformer_sw_fp16.onnx
rem By default downloads BS-Rofo-SW-Fixed.ckpt / .yaml (Hugging Face mirror, sha256 verified).
rem Or pass local paths:
rem   export-onnx.bat --checkpoint path.ckpt --config path.yaml

set "VENV=%~dp0.venv"
set "PY="
set "CHECKPOINT="
set "CONFIG="
set "SKIP_DOWNLOAD="
set "EXITCODE=0"

:parse
if "%~1"=="" goto :parsed
if /I "%~1"=="--checkpoint" (
  if "%~2"=="" (
    echo Missing path after --checkpoint.
    set "EXITCODE=1"
    goto :finish
  )
  set "CHECKPOINT=%~2"
  shift
  shift
  goto :parse
)
if /I "%~1"=="--config" (
  if "%~2"=="" (
    echo Missing path after --config.
    set "EXITCODE=1"
    goto :finish
  )
  set "CONFIG=%~2"
  shift
  shift
  goto :parse
)
if /I "%~1"=="--skip-download" (
  set "SKIP_DOWNLOAD=1"
  shift
  goto :parse
)
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="--help" goto :usage
echo Unknown argument: %~1
goto :usage

:usage
echo Usage: export-onnx.bat [--checkpoint path.ckpt] [--config path.yaml] [--skip-download]
echo.
echo Without paths, downloads BS-Rofo-SW-Fixed.ckpt / .yaml into the repo root
echo ^(skips when present and sha256 matches^).
echo --skip-download uses local files only and does not fetch BS-RoFormer weights.
set "EXITCODE=1"
goto :finish

:parsed
call :find_python
if errorlevel 1 goto :fail

echo Using Python: %PY%
"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo Python 3.11+ is required. demucs-onnx export does not run on older versions.
  set "EXITCODE=1"
  goto :finish
)

if not exist "%VENV%\Scripts\python.exe" (
  echo.
  echo Creating virtualenv: .venv
  "%PY%" -m venv "%VENV%"
  if errorlevel 1 goto :fail
) else (
  echo.
  echo Reusing virtualenv: .venv
)

set "VPY=%VENV%\Scripts\python.exe"
set "VPIP=%VENV%\Scripts\python.exe"

echo.
echo Installing / updating export dependencies...
"%VPIP%" -m pip install -U pip
if errorlevel 1 goto :fail
"%VPIP%" -m pip install -r "%~dp0requirements-bs-roformer-onnx.txt"
if errorlevel 1 goto :fail
"%VPIP%" -m pip install "demucs-onnx[export]==0.3.4" "onnxruntime>=1.20" soundfile
if errorlevel 1 goto :fail
rem demucs-onnx may pull a newer onnxscript; reassert the pin for Torch 2.4.
"%VPIP%" -m pip install "onnxscript>=0.5.0,<0.6"
if errorlevel 1 goto :fail

rem Avoid torch.onnx + beartype crashing when type hints lag onnxscript APIs.
set "TORCH_ONNX_EXPERIMENTAL_RUNTIME_TYPE_CHECK=DISABLED"

if not exist "models" mkdir models

if not defined SKIP_DOWNLOAD (
  echo.
  echo Downloading / verifying BS-RoFormer SW weights ^(~700 MB; skips if sha256 matches^)...
  "%VPY%" "%~dp0tools\fetch_bs_roformer_sw.py" --output-dir "%~dp0."
  if errorlevel 1 goto :fail
)

if not defined CHECKPOINT if exist "BS-Rofo-SW-Fixed.ckpt" set "CHECKPOINT=%~dp0BS-Rofo-SW-Fixed.ckpt"
if not defined CONFIG if exist "BS-Rofo-SW-Fixed.yaml" set "CONFIG=%~dp0BS-Rofo-SW-Fixed.yaml"

if not defined CHECKPOINT (
  echo BS-RoFormer checkpoint not found. Drop --skip-download, or pass --checkpoint.
  set "EXITCODE=1"
  goto :finish
)
if not defined CONFIG (
  echo BS-RoFormer config not found. Drop --skip-download, or pass --config.
  set "EXITCODE=1"
  goto :finish
)
if not exist "%CHECKPOINT%" (
  echo Checkpoint does not exist: %CHECKPOINT%
  set "EXITCODE=1"
  goto :finish
)
if not exist "%CONFIG%" (
  echo Config does not exist: %CONFIG%
  set "EXITCODE=1"
  goto :finish
)

echo.
echo [1/3] Transcription model nmp.onnx
"%VPY%" "%~dp0tools\fetch_nmp_onnx.py"
if errorlevel 1 goto :fail

echo.
echo [2/3] HTDemucs 6s -^> models\htdemucs_6s.onnx
"%VPY%" -m htdemucs6s_onnx export "%~dp0models\htdemucs_6s.onnx"
if errorlevel 1 goto :fail

echo.
echo [3/3] BS-RoFormer SW -^> models\bs_roformer_sw.onnx ^(+ fp16^)
"%VPY%" -m bs_roformer_sw_onnx export "%~dp0models\bs_roformer_sw.onnx" --checkpoint "%CHECKPOINT%" --config "%CONFIG%" --fp16 "%~dp0models\bs_roformer_sw_fp16.onnx"
if errorlevel 1 goto :fail

echo.
echo Done. models\ should contain:
for %%F in (nmp.onnx htdemucs_6s.onnx bs_roformer_sw.onnx bs_roformer_sw_fp16.onnx) do (
  if exist "models\%%F" (
    echo   OK  models\%%F
  ) else (
    echo   MISSING  models\%%F
    set "EXITCODE=1"
  )
)
if not "%EXITCODE%"=="0" goto :fail
goto :finish

:find_python
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
  for %%V in (3.13 3.12 3.11) do (
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
    if not defined PY (
      "%%P" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
      if not errorlevel 1 set "PY=%%P"
    )
  )
)
if defined PY exit /b 0

for %%P in (
  "%LocalAppData%\Programs\Python\Python313\python.exe"
  "%LocalAppData%\Programs\Python\Python312\python.exe"
  "%LocalAppData%\Programs\Python\Python311\python.exe"
  "%UserProfile%\anaconda3\envs\bocchi\python.exe"
  "%UserProfile%\miniconda3\python.exe"
) do (
  if not defined PY if exist "%%~P" (
    "%%~P" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=%%~P"
  )
)
if defined PY exit /b 0

echo Python 3.11+ not found. Install it and ensure py or python is on PATH.
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
