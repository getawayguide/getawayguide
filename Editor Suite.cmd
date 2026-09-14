@echo off
REM The whole editor suite from one file. Double-click to start the photo
REM server, hero picker and backup watcher (skipping whatever is already up)
REM and open editor.html. Pause/resume the backup and fix iCloud sync from
REM the editor's Live Activity panel, or from here:
REM
REM   "Editor Suite.cmd" pause  |  resume  |  fix  |  status  |  stop  |  site
REM
REM Everything it does lives in tools\photo_suite.py.
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "tools\photo_suite.py" %*
if not "%~1"=="" pause
if errorlevel 1 pause
