@echo off
setlocal

set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%..\..") do set REPO_ROOT=%%~fI

cd /d "%REPO_ROOT%"

where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv not found in PATH.
    echo Please install uv first, then rerun this script.
    pause
    exit /b 1
)

if not exist "services\bilibili_gateway\config\service.toml" (
    echo [ERROR] Missing config file: services\bilibili_gateway\config\service.toml
    pause
    exit /b 1
)

findstr /c:"mock_mode = true" "services\bilibili_gateway\config\service.toml" >nul 2>nul
if not errorlevel 1 (
    echo [WARN] mock_mode is still true in services\bilibili_gateway\config\service.toml
    echo        Switch it to false before real-room integration.
    echo.
)

echo [INFO] Starting bilibili_gateway from %REPO_ROOT%
echo [INFO] Command: uv run python -m services.bilibili_gateway.main serve
echo.

uv run python -m services.bilibili_gateway.main serve
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] bilibili_gateway exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
