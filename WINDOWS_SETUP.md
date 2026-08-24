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

After it succeeds, validate configuration before starting the emulator:

```bat
.venv\Scripts\activate.bat
ps2-autopilot-doctor --config config\madden2005.yaml --config-only
```

`--config-only` does not require Windows capture, PCSX2, OCR, PINE, or a controller device. It validates the registered profile, controller backend, supervisor settings, and—when emulator relaunch is enabled—the configured PCSX2 working directory and executable/PATH resolution. This is the preferred first check after editing unattended-supervisor settings.

Then start PCSX2 and run the full live doctor:

```bat
ps2-autopilot-doctor --config config\madden2005.yaml
```

The full doctor verifies the render window, frame capture, runtime controller dependency, and game-specific live prerequisites.

Then:

```bat
ps2-autopilot --config config\madden2005.yaml
```

For supervised 24/7 operation, use `run24x7.cmd` / `ps2-autopilot-supervisor` after the relevant local emulator command is configured. See `SUPERVISOR.md`; machine-specific PCSX2 and game-image paths stay local and are not committed.

## Existing clone

After a repo update, rerun bootstrap so newly-added dependencies are installed:

```bat
git pull
bootstrap.cmd
.venv\Scripts\activate.bat
```

Run the static preflight again after changing profile, controller, or supervisor configuration:

```bat
ps2-autopilot-doctor --config config\madden2005.yaml --config-only
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
