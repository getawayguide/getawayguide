@echo off
REM Stops the photo backup so the machine is entirely yours. Nothing is lost -
REM every photo already downloaded stays put, and "Start Photo Tools.cmd"
REM picks up exactly where this left off.
echo Stopping the photo backup...
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'photo_backup' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo.
echo Backup paused. Run "Start Photo Tools.cmd" to resume.
timeout /t 3 >nul
