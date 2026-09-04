@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call run24x7.cmd config\madden2005.yaml
exit /b %ERRORLEVEL%
