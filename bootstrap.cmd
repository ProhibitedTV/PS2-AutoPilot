@echo off
setlocal
cd /d "%~dp0"

echo [PS2 AutoPilot] Windows bootstrap

echo [1/4] Checking Python 3.11...
where py >nul 2>&1
if errorlevel 1 goto :no_py

py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto :no_311

for /f "delims=" %%P in ('py -3.11 -c "import sys; print(sys.executable)"') do set PY311=%%P
echo       Found: %PY311%

if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Creating .venv with Python 3.11...
  py -3.11 -m venv .venv
  if errorlevel 1 goto :failed
) else (
  echo [2/4] Existing .venv found.
  ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo       Existing .venv was created with an older Python. Rebuilding it...
    rmdir /s /q .venv
    py -3.11 -m venv .venv
    if errorlevel 1 goto :failed
  )
)

echo [3/4] Updating packaging tools...
".venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :failed

echo [4/4] Installing PS2 AutoPilot and virtual gamepad support...
".venv\Scripts\python.exe" -m pip install -e ".[virtual-gamepad]"
if errorlevel 1 goto :failed

echo.
echo [OK] Installation complete.
echo.
echo In Command Prompt, activate with:
echo   .venv\Scripts\activate.bat
echo.
echo Then boot PCSX2 and run:
echo   ps2-autopilot-doctor --config config\madden2005.yaml
echo.
goto :eof

:no_py
echo.
echo [ERROR] The Python launcher ^(py.exe^) was not found.
echo Install 64-bit Python 3.11 from python.org and enable the Python launcher,
echo then reopen Command Prompt and run bootstrap.cmd again.
exit /b 1

:no_311
echo.
echo [ERROR] Python 3.11 is not installed.
echo The current project requires Python 3.11 or newer.
echo.
echo See installed Python versions with:
echo   py -0p
echo.
echo Install 64-bit Python 3.11, reopen Command Prompt, and run:
echo   bootstrap.cmd
exit /b 1

:failed
echo.
echo [ERROR] Bootstrap failed. Copy the output above when reporting the problem.
exit /b 1
