# Windows setup

PS2 AutoPilot requires **64-bit Python 3.11 or newer**. The repository includes `bootstrap.cmd` so Command Prompt users do not need to manually create or activate the virtual environment before installation.

## Fast path — Command Prompt (`cmd.exe`)

From the repository folder:

```bat
bootstrap.cmd
```

The bootstrap script:

1. checks that the Windows `py` launcher exists;
2. checks specifically for Python 3.11;
3. creates `.venv` with Python 3.11 (or rebuilds an old incompatible `.venv`);
4. upgrades pip/setuptools/wheel inside that venv;
5. installs PS2 AutoPilot with virtual-gamepad support.

After it succeeds:

```bat
.venv\Scripts\activate.bat
ps2-autopilot-doctor --config config\madden2005.yaml
```

Then boot Madden 2005 in PCSX2 and run:

```bat
ps2-autopilot --config config\madden2005.yaml
```

## PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[virtual-gamepad]"
```

## If installation says `setuptools>=69` cannot be found

That almost always means the `pip` command is attached to an older Python interpreter. Check what Windows has installed:

```bat
python --version
py -0p
```

Do **not** use the global `pip install -e ...` command until the Python 3.11 venv is active. Prefer `bootstrap.cmd`, which always invokes the venv's Python directly.

## Activation syntax matters

Command Prompt:

```bat
.venv\Scripts\activate.bat
```

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Also note the path is `.venv`, with a dot followed immediately by `venv`; `..venv` is a different path and will fail.
