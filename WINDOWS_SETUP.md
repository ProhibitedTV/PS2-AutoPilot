# Windows setup

PS2 AutoPilot requires **64-bit Python 3.11 or newer**. Python 3.11 or 3.12 is recommended for the current full Madden OCR stack.

## Fast path — Command Prompt

From the repository folder:

```bat
bootstrap.cmd
```

The bootstrap script:

1. finds a compatible Python, including split `python` / `python3` / Conda installations;
2. creates or repairs `.venv`;
3. upgrades pip/setuptools/wheel;
4. installs PS2 AutoPilot's `full` extra:
   - virtual Xbox controller support
   - local RapidOCR semantic vision

After it succeeds:

```bat
.venv\Scripts\activate.bat
ps2-autopilot-doctor --config config\madden2005.yaml
```

Then:

```bat
ps2-autopilot --config config\madden2005.yaml
```

## Existing clone

After a repo update, rerun bootstrap so newly-added dependencies are installed:

```bat
git pull
bootstrap.cmd
.venv\Scripts\activate.bat
```

## Manual install

Inside a compatible venv:

```bat
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[full]"
```

For the lighter build without OCR:

```bat
python -m pip install -e ".[virtual-gamepad]"
```

The Madden profile will still run without OCR, but semantic menu routing and scoreboard/down-distance reading will be unavailable.

## Finding Python on a multi-Python Windows machine

```bat
py -0p
where py
where python
python --version
where python3
python3 --version
```

To resolve an alias to the actual interpreter:

```bat
python3 -c "import sys; print(sys.executable); print(sys.version)"
```

Bootstrap also checks common Conda locations:

```text
%USERPROFILE%\anaconda3\python.exe
%USERPROFILE%\miniconda3\python.exe
%USERPROFILE%\pinokio\bin\miniconda\python.exe
```

## Activation syntax

Command Prompt:

```bat
.venv\Scripts\activate.bat
```

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

## If `setuptools>=69` cannot be found

Your global `pip` is probably attached to an older Python. Do not use bare global `pip` for this project. Activate `.venv`, or rerun:

```bat
bootstrap.cmd
```
