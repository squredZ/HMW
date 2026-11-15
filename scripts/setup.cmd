@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
where uv >nul 2>&1 || (echo Missing uv. Install uv and run this script again. 1>&2 & exit /b 1)
where node >nul 2>&1 || (echo Missing Node.js. Install Node.js and run this script again. 1>&2 & exit /b 1)
where pnpm >nul 2>&1 || (echo Missing pnpm. Install pnpm and run this script again. 1>&2 & exit /b 1)
echo Installing backend dependencies...
call uv sync --locked --project "%PROJECT_ROOT%\apps\api"
if errorlevel 1 exit /b 1
echo Installing frontend dependencies...
call pnpm --dir "%PROJECT_ROOT%\apps\web" install --frozen-lockfile
if errorlevel 1 exit /b 1
echo Setup complete. See README.md for configuration and startup.
exit /b 0
