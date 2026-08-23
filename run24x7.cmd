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
