@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "PYTHON=%PROJECT_ROOT%\apps\api\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (echo Backend environment not found. Run scripts\setup.cmd first. 1>&2 & exit /b 1)
set "CONFIG=%~1"
if not defined CONFIG set "CONFIG=%PROJECT_ROOT%\.hmw.toml"
"%PYTHON%" "%PROJECT_ROOT%\scripts\start.py" "%CONFIG%"
exit /b %errorlevel%
