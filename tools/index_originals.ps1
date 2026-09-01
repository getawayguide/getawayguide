# Index the FULL-RESOLUTION iCloud library by capture timestamp - without
# downloading anything.
#
# iCloud for Windows keeps the library as cloud-only placeholders (~120GB).
# Reading a file's bytes forces a full download, but the Windows property
# system serves "Date taken" and "Dimensions" straight from the placeholder,
# so we can build a complete index for free (~80s for 21k photos).
#
# tools/photo_editor.py uses the result to swap a small Shared-Album copy
# (2048px) for its full-resolution original at import time - matched on the
# capture timestamp, which both files share.
#
#   powershell -ExecutionPolicy Bypass -File tools\index_originals.ps1
param(
  [string]$Library = "$env:USERPROFILE\iCloudPhotos\Photos",
  [string]$Out = "$PSScriptRoot\..\.tmp\photo_editor\originals_index.json"
)

if (-not (Test-Path $Library)) { Write-Error "library not found: $Library"; exit 1 }
$outDir = Split-Path $Out -Parent
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Force $outDir | Out-Null }

$shell = New-Object -ComObject Shell.Application
$folder = $shell.Namespace((Resolve-Path $Library).Path)
$DIMS = 31

$exts = @('.heic', '.heif', '.jpg', '.jpeg', '.png')
$files = Get-ChildItem $Library -File | Where-Object { $exts -contains $_.Extension.ToLower() }
Write-Host "library: $Library"
Write-Host "photos:  $($files.Count)"

$sw = [Diagnostics.Stopwatch]::StartNew()
$map = @{}
$i = 0
$noDate = 0
foreach ($f in $files) {
  $i++
  $item = $folder.ParseName($f.Name)
  if (-not $item) { continue }
  $dt = $null
  try { $dt = $item.ExtendedProperty("System.Photo.DateTaken") } catch { }
  if (-not $dt) { $noDate++; continue }
  # DateTaken comes back as UTC; keep a stable sortable key to the second
  $key = ([datetime]$dt).ToString("yyyy-MM-ddTHH:mm:ss")
  $dims = $folder.GetDetailsOf($item, $DIMS) -replace '[^\dx ]', ''
  $w = 0; $h = 0
  if ($dims -match '(\d+)\s*x\s*(\d+)') { $w = [int]$Matches[1]; $h = [int]$Matches[2] }
  # several photos can share a second (bursts): keep a list per timestamp
  if (-not $map.ContainsKey($key)) { $map[$key] = New-Object System.Collections.ArrayList }
  [void]$map[$key].Add([PSCustomObject]@{ n = $f.Name; w = $w; h = $h; px = ($w * $h) })
  if ($i % 2000 -eq 0) {
    Write-Host ("  {0}/{1}  ({2:N0}/s)" -f $i, $files.Count, ($i / $sw.Elapsed.TotalSeconds))
  }
}
$sw.Stop()

$payload = [PSCustomObject]@{
  library = (Resolve-Path $Library).Path
  built   = (Get-Date).ToString("s")
  count   = $i
  byTime  = $map
}
$payload | ConvertTo-Json -Depth 6 -Compress | Set-Content -Path $Out -Encoding utf8
Write-Host ("indexed {0} photos in {1:N0}s ({2} had no capture date)" -f $i, $sw.Elapsed.TotalSeconds, $noDate)
Write-Host "wrote $Out"
