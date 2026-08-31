@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call run24x7.cmd config\nfs_hot_pursuit_2.yaml
exit /b %ERRORLEVEL%
