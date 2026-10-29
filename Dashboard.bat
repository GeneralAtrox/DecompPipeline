@echo off
rem Builds the progress dashboard from every game workspace and opens it in your browser (tools/dashboard.py).
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3.12"
%PY% tools\dashboard.py %*
if errorlevel 1 pause
