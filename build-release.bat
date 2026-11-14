@echo off
call "%~dp0build-cpp.bat" Release
exit /b %ERRORLEVEL%
