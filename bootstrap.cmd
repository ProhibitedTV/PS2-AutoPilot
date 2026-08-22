@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PYTHON_EXE="

echo [PS2 AutoPilot] Windows bootstrap

echo [1/4] Finding compatible Python 3.11+...

rem Prefer the Windows Python launcher when present. `py -3` selects the
rem newest registered Python 3 installation; do not require exactly 3.11.
where py >nul 2>&1
if not errorlevel 1 (
  for /f "usebackq delims=" %%P in (`py -3 -c "import sys; (print(sys.executable) if sys.version_info >= (3,11) else sys.exit(1))" 2^>nul`) do set "PYTHON_EXE=%%P"
)

rem Fall back to python.exe on PATH.
if not defined PYTHON_EXE (
  where python >nul 2>&1
  if not errorlevel 1 (
    for /f "usebackq delims=" %%P in (`python -c "import sys; (print(sys.executable) if sys.version_info >= (3,11) else sys.exit(1))" 2^>nul`) do set "PYTHON_EXE=%%P"
  )
)

rem Finally probe common per-user and machine-wide python.org install paths.
if not defined PYTHON_EXE (
  for %%P in (
    "%LocalAppData%\Programs\Python\Python314\python.exe"
    "%LocalAppData%\Programs\Python\Python313\python.exe"
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%ProgramFiles%\Python314\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
  ) do (
    if not defined PYTHON_EXE if exist "%%~P" (
      "%%~P" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
      if not errorlevel 1 set "PYTHON_EXE=%%~P"
    )
  )
)

if not defined PYTHON_EXE goto :no_compatible_python

for /f "delims=" %%V in ('"!PYTHON_EXE!" -c "import platform; print(platform.python_version())"') do set "PYTHON_VERSION=%%V"
echo       Found Python !PYTHON_VERSION!: !PYTHON_EXE!

if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Creating .venv...
  "!PYTHON_EXE!" -m venv .venv
  if errorlevel 1 goto :failed
) else (
  echo [2/4] Existing .venv found.
  ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo       Existing .venv uses an incompatible Python. Rebuilding it...
    rmdir /s /q .venv
    "!PYTHON_EXE!" -m venv .venv
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

:no_compatible_python
echo.
echo [ERROR] Could not locate a usable Python 3.11 or newer installation.
echo.
echo Diagnostic commands:
echo   py -0p
echo   where py
echo   where python
echo   python --version
echo.
echo If Python is installed in a custom folder, either add that folder to PATH
 echo or recreate the venv explicitly with the full executable path, for example:
echo   "C:\Path\To\Python\python.exe" -m venv .venv
echo.
echo If no Python 3.11+ installation appears, install a current 64-bit Python,
echo reopen Command Prompt, and run bootstrap.cmd again.
exit /b 1

:failed
echo.
echo [ERROR] Bootstrap failed. Copy the output above when reporting the problem.
exit /b 1
