@echo off
REM Starts the two background helpers the photo tools need, then gets out of
REM the way. Safe to double-click any time - it skips whatever is already up.
REM   * photo server    (port 5003): photos for editor.html (editor + library)
REM   * backup watcher: pulls full-res originals for each new Shared Album
cd /d "%~dp0"

set "PYW=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"

netstat -an | find "127.0.0.1:5003" | find "LISTENING" >nul
if errorlevel 1 (
  echo Starting photo server...
  REM -u + redirect, same reasoning as the watcher below: under a bare pythonw
  REM a crash is SILENT, and when this server dies every photo in the editor
  REM just stops loading with nothing to explain why.
  if not exist "%USERPROFILE%\Backup\_meta" mkdir "%USERPROFILE%\Backup\_meta"
  start "" "%PYW%" -u "tools\photo_editor.py" 1>>"%USERPROFILE%\Backup\_meta\editor.log" 2>>"%USERPROFILE%\Backup\_meta\editor.err"
) else (
  echo Photo server already running.
)

wmic process where "name='python.exe' or name='pythonw.exe'" get commandline 2>nul | find "photo_backup" >nul
if errorlevel 1 (
  echo Starting backup watcher...
  REM -u + redirect: under a bare pythonw a crash is SILENT. The watcher
  REM died once on an unhandled error and nothing said so - the log is the
  REM only way to find out afterwards.
  if not exist "%USERPROFILE%\Backup\_meta" mkdir "%USERPROFILE%\Backup\_meta"
  start "" "%PYW%" -u "tools\photo_backup.py" --watch --workers 4 --interval 90 1>>"%USERPROFILE%\Backup\_meta\watcher.log" 2>>"%USERPROFILE%\Backup\_meta\watcher.err"
) else (
  echo Backup watcher already running.
)

REM backup_watchdog.py is deliberately NOT started here. Restarting the
REM watcher abandons its in-flight copies, and those keep their place in
REM iCloud's hydration queue as ghosts - so every restart pushed the fresh
REM workers further back in line and made stalls WORSE. The watcher now
REM handles slow hydrations itself (it waits them out instead of killing
REM them) and does not need an external restarter.

echo.
echo Ready. Opening the editor (photo library lives inside it now)...
timeout /t 3 /nobreak >nul
start "" "%~dp0editor.html"
