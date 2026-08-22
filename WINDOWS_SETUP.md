# Windows setup

PS2 AutoPilot requires **64-bit Python 3.11 or newer**. The repository includes `bootstrap.cmd` so Command Prompt users do not need to manually create or activate the virtual environment before installation.

## Fast path — Command Prompt (`cmd.exe`)

From the repository folder:

```bat
bootstrap.cmd
```

The bootstrap script:

1. looks for any compatible Python **3.11+** installation;
2. prefers the Windows `py` launcher, then `python.exe` on PATH, then common python.org install folders;
3. creates `.venv` with the compatible interpreter (or rebuilds an old incompatible `.venv`);
4. upgrades pip/setuptools/wheel inside that venv;
5. installs PS2 AutoPilot with virtual-gamepad support.

It does **not** require exactly Python 3.11. Python 3.12, 3.13, and newer compatible Python 3 releases can be used as long as the project's dependencies support them.

After it succeeds:

```bat
.venv\Scripts\activate.bat
ps2-autopilot-doctor --config config\madden2005.yaml
```

Then boot Madden 2005 in PCSX2 and run:

```bat
ps2-autopilot --config config\madden2005.yaml
```

## Find an existing Python installation

If bootstrap cannot locate Python, run:

```bat
py -0p
where py
where python
python --version
```

`py -0p` is especially useful because the Windows Python launcher can know about Python installations that are not on PATH.

If Python is installed in a custom location, you can create the venv directly with its full path:

```bat
"C:\Path\To\Python\python.exe" -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[virtual-gamepad]"
```

## PowerShell

If the `py` launcher knows about a compatible Python, this works with any selected 3.11+ interpreter. For example, with Python 3.12:

```powershell
py -3.12 -m venv .venv
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

Do **not** use the global `pip install -e ...` command until the Python 3.11+ venv is active. Prefer `bootstrap.cmd`, which always invokes the venv's Python directly.

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
