# Windows setup

PS2 AutoPilot requires **64-bit Python 3.11 or newer**. The repository includes `bootstrap.cmd` so Command Prompt users do not need to manually create or activate the virtual environment before installation.

## Fast path — Command Prompt (`cmd.exe`)

From the repository folder:

```bat
bootstrap.cmd
```

The bootstrap script:

1. looks for any compatible Python **3.11+** installation;
2. prefers the Windows `py` launcher, then `python.exe` on PATH, then `python3.exe`, an active Conda environment, and common python.org/Conda install folders;
3. resolves aliases through `sys.executable` so Windows App Execution Aliases can still lead to the real interpreter;
4. creates `.venv` with the compatible interpreter (or rebuilds an old incompatible `.venv`);
5. upgrades pip/setuptools/wheel inside that venv;
6. installs PS2 AutoPilot with virtual-gamepad support.

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
where python3
python3 --version
```

`py -0p` is especially useful because the Windows Python launcher can know about Python installations that are not on PATH. On systems with several historical Python installs, it is also possible for `python` to resolve to an old interpreter while `python3` resolves to a newer Conda or Windows App Execution Alias interpreter.

To see the real executable behind a working alias:

```bat
python3 -c "import sys; print(sys.executable); print(sys.version)"
```

Common Conda interpreter locations checked by bootstrap include:

```text
%USERPROFILE%\anaconda3\python.exe
%USERPROFILE%\miniconda3\python.exe
%USERPROFILE%\pinokio\bin\miniconda\python.exe
```

If Python is installed in another custom location, you can create the venv directly with its full path:

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
python3 --version
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
