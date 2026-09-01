# Index the full-resolution VIDEOS in the iCloud library by capture time.
#
# Companion to index_originals.ps1 (which handles photos). Videos need their
# own pass because they carry System.Media.DateEncoded rather than
# System.Photo.DateTaken - and, crucially, because a Shared Album's video
# copies are re-encoded and LOSE their original capture time, so a video can
# only be traced back to its original through the trip's date window.
#
# Reads placeholder metadata only: nothing is downloaded.
#
#   powershell -ExecutionPolicy Bypass -File tools\index_videos.ps1
param(
  [string]$Library = "$env:USERPROFILE\iCloudPhotos\Photos",
  [string]$Out = "$PSScriptRoot\..\.tmp\photo_editor\videos_index.json"
)

if (-not (Test-Path $Library)) { Write-Error "library not found: $Library"; exit 1 }
$outDir = Split-Path $Out -Parent
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Force $outDir | Out-Null }

$shell = New-Object -ComObject Shell.Application
$folder = $shell.Namespace((Resolve-Path $Library).Path)

$vids = Get-ChildItem $Library -File | Where-Object { $_.Extension -match '^\.(mp4|mov|m4v)$' }
Write-Host "videos in library: $($vids.Count)"

$list = New-Object System.Collections.ArrayList
$noDate = 0
$sw = [Diagnostics.Stopwatch]::StartNew()
foreach ($v in $vids) {
  $item = $folder.ParseName($v.Name)
  if (-not $item) { continue }
  $dt = $null
  foreach ($prop in @("System.Media.DateEncoded", "System.Photo.DateTaken", "System.ItemDate")) {
    try { $dt = $item.ExtendedProperty($prop) } catch { }
    if ($dt) { break }
  }
  if (-not $dt) { $noDate++; continue }
  $dur = 0
  try { $d = $item.ExtendedProperty("System.Media.Duration"); if ($d) { $dur = [int64]$d } } catch { }
  [void]$list.Add([PSCustomObject]@{
    n   = $v.Name
    t   = ([datetime]$dt).ToString("yyyy-MM-ddTHH:mm:ss")
    sz  = $v.Length
    dur = $dur          # 100-ns ticks; survives Apple's re-encode, so it
  })                    # identifies a shared copy's original exactly
}
$sw.Stop()

[PSCustomObject]@{
  library = (Resolve-Path $Library).Path
  built   = (Get-Date).ToString("s")
  count   = $list.Count
  videos  = $list
} | ConvertTo-Json -Depth 5 -Compress | Set-Content -Path $Out -Encoding utf8

Write-Host ("indexed {0} videos in {1:N0}s ({2} had no date)" -f $list.Count, $sw.Elapsed.TotalSeconds, $noDate)
Write-Host "wrote $Out"
