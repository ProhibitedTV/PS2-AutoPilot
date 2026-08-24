@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "CONFIG=%~1"
if "%CONFIG%"=="" set "CONFIG=config\madden2005.yaml"

if not exist "%CONFIG%" (
  echo [ERROR] Config not found: %CONFIG%
  exit /b 1
)

echo [PS2 AutoPilot] 24/7 supervisor
echo [PS2 AutoPilot] Config: %CONFIG%

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] No virtual environment found. Running bootstrap first...
  call bootstrap.cmd
  if errorlevel 1 goto :failed
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :failed

rem A manually launched supervisor marks a new stream session. Clear the previous
rem session once here. The Python supervisor owns all subsequent AutoPilot and
rem optional PCSX2 restart attempts, preserving current-session crash evidence.
echo [PS2 AutoPilot] Clearing previous runtime artifacts...
python -m ps2_autopilot.runtime_retention --root runtime --clear --max-total-mb 300 --max-failures 30 --max-unknown 60
if errorlevel 1 goto :failed

echo.
echo [%date% %time%] Starting supervisor with %CONFIG%...
python -m ps2_autopilot.supervisor_cli --config "%CONFIG%"
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" goto :stopped

echo.
echo [ERROR] Supervisor exited with code %EXIT_CODE%.
exit /b %EXIT_CODE%

:stopped
echo.
echo [PS2 AutoPilot] 24/7 supervisor stopped.
exit /b 0

:failed
echo.
echo [ERROR] Could not start the 24/7 supervisor.
exit /b 1
