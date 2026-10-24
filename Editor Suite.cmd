@echo off
REM The whole editor suite from one file. Double-click to start the photo
REM server, hero picker and map editor (skipping whatever is already up)
REM and open editor.html. Or from here:
REM
REM   "Editor Suite.cmd" status  |  stop  |  site
REM
REM Everything it does lives in tools\photo_suite.py.
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "tools\photo_suite.py" %*
if not "%~1"=="" pause
if errorlevel 1 pause
