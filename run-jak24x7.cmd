@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call run24x7.cmd config\jak_and_daxter.yaml
exit /b %ERRORLEVEL%
