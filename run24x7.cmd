@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [PS2 AutoPilot] 24/7 Madden runner

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] No virtual environment found. Running bootstrap first...
  call bootstrap.cmd
  if errorlevel 1 goto :failed
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :failed

rem A manually launched runner marks a new stream session. Clear the previous
rem session's logs/screenshots once here, before the restart loop. Automatic
rem crash restarts jump back to :run and therefore preserve the current crash
rem evidence instead of immediately deleting it.
echo [PS2 AutoPilot] Clearing previous runtime artifacts...
python -m ps2_autopilot.runtime_retention --root runtime --clear --max-total-mb 300 --max-failures 30 --max-unknown 60
if errorlevel 1 goto :failed

:run
if exist "runtime\STOP24X7" goto :stopped

echo.
echo [%date% %time%] Starting Madden AutoPilot...
ps2-autopilot --config config\madden2005.yaml
set "EXIT_CODE=%ERRORLEVEL%"

rem Ctrl+C is handled by AutoPilot as a clean exit. Do not immediately restart
rem a process the operator intentionally stopped.
if "%EXIT_CODE%"=="0" goto :stopped

echo [%date% %time%] AutoPilot exited with code %EXIT_CODE%.
echo Restarting in 5 seconds. Press Ctrl+C to stop the runner.
timeout /t 5 /nobreak >nul
goto :run

:stopped
echo.
echo [PS2 AutoPilot] 24/7 runner stopped.
if exist "runtime\STOP24X7" del /q "runtime\STOP24X7" >nul 2>&1
exit /b 0

:failed
echo.
echo [ERROR] Could not start the 24/7 runner.
exit /b 1
