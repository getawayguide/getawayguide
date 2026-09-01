@echo off
REM Restarts BOTH iCloud photo services when syncing stops.
REM
REM   ApplePhotoStreams.exe  - shared albums
REM   iCloudPhotos.exe       - the main library (this is what hydrates the
REM                            full-resolution originals the backup copies)
REM
REM Two separate faults, both fixed by a restart:
REM
REM 1. STARTUP RACE (ApplePhotoStreams). Windows launches it twice ~30s apart;
REM    the second - the real worker - exits on a single-instance check
REM    ("Another instance of the asset uploader is already running") and the
REM    survivor is a COM stub started with -Embedding that never syncs. Shared
REM    albums stop growing while iCloudPhotos burns CPU, so Task Manager looks
REM    busy. Rebooting does NOT help - the same race happens every boot.
REM
REM 2. SLOW DECAY (both, but especially iCloudPhotos). After many hours up it
REM    keeps running but throughput collapses - one connection instead of a
REM    dozen, and cloud hydrations start failing with WinError 426 "The cloud
REM    operation was not completed before the time-out period expired". Seen
REM    after ~20h uptime / ~9000 CPU-seconds: the backup went to literally zero
REM    files per 90 seconds, and jumped straight back after a restart.
REM
REM Telltale for both: few or zero established network connections and CPU
REM that is not climbing. Healthy is CPU in bursts with several connections.
REM
REM The exe paths come from Get-AppxPackage, not a glob: WindowsApps refuses
REM directory listing to a normal user, so globbing finds nothing even though
REM the files are there and launchable.
REM
REM Safe to run any time. An in-flight backup copy just fails and is retried
REM on the next pass.
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$pkg = Get-AppxPackage -Name 'AppleInc.iCloud' -ErrorAction SilentlyContinue;" ^
  "if (-not $pkg) { Write-Host 'iCloud does not appear to be installed from the Store.'; exit 1 };" ^
  "foreach ($name in 'ApplePhotoStreams','iCloudPhotos') {" ^
  "  $p = Get-Process $name -ErrorAction SilentlyContinue;" ^
  "  if ($p) { Write-Host ('Stopping {0} PID {1} (CPU {2:N0}s, up {3:N0} min)' -f $name, $p.Id, $p.CPU, ((Get-Date)-$p.StartTime).TotalMinutes); Stop-Process -Id $p.Id -Force }" ^
  "  else { Write-Host ($name + ' was not running.') };" ^
  "};" ^
  "Start-Sleep -Seconds 4;" ^
  "foreach ($name in 'ApplePhotoStreams','iCloudPhotos') {" ^
  "  $exe = Join-Path $pkg.InstallLocation ('iCloud\' + $name + '.exe');" ^
  "  if (Test-Path $exe) { Start-Process $exe } else { Write-Host ('Not found: ' + $exe) };" ^
  "};" ^
  "Write-Host '';" ^
  "Write-Host 'Waiting 45s to see whether they settle...';" ^
  "Start-Sleep -Seconds 45;" ^
  "$bad = 0;" ^
  "foreach ($name in 'ApplePhotoStreams','iCloudPhotos') {" ^
  "  $n = Get-Process $name -ErrorAction SilentlyContinue;" ^
  "  if (-not $n) { Write-Host ($name + ': did not stay running.'); $bad++; continue };" ^
  "  $c = (Get-NetTCPConnection -State Established -OwningProcess $n.Id -ErrorAction SilentlyContinue ^| Measure-Object).Count;" ^
  "  Write-Host ('{0}: PID {1}, CPU {2:N1}s, {3} connections' -f $name, $n.Id, $n.CPU, $c);" ^
  "  if ($n.CPU -lt 2 -and $c -eq 0) { Write-Host ('  ' + $name + ' still looks idle.'); $bad++ }" ^
  "};" ^
  "Write-Host '';" ^
  "if ($bad -gt 0) { Write-Host 'Not fully healthy - run this once more.' } else { Write-Host 'Healthy. Downloads and backups should move again.' }"

echo.
pause
