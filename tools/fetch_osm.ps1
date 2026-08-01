# Reads .tmp/fetchlist.txt (lines: "<slug> <S,W,N,E>") and fetches OSM to .tmp/<slug>_osm.json
$ProgressPreference = 'SilentlyContinue'
foreach ($line in Get-Content ".tmp\fetchlist.txt") {
  $line = $line.Trim()
  if ($line -eq "") { continue }
  $parts = $line -split '\s+'
  $slug = $parts[0]; $bbox = $parts[1]
  $out = "$PWD\.tmp\$($slug)_osm.json"
  if (Test-Path $out) { $sz = [math]::Round((Get-Item $out).Length / 1MB, 1); if ($sz -gt 0.05) { Write-Output "skip $slug (have $sz MB)"; continue } }
  $q = @"
[out:json][timeout:180];
(
  way["natural"="water"]($bbox);
  relation["natural"="water"]($bbox);
  way["natural"="coastline"]($bbox);
  way["waterway"~"river|canal|riverbank"]($bbox);
  way["leisure"="park"]($bbox);
  way["leisure"="garden"]($bbox);
  way["landuse"~"forest|grass|recreation_ground|cemetery"]($bbox);
  way["highway"~"motorway|trunk|primary|secondary|tertiary|residential|unclassified|living_street|pedestrian"]($bbox);
);
out geom;
"@
  $ok = $false
  for ($i = 1; $i -le 4; $i++) {
    try {
      Invoke-WebRequest -Uri "https://overpass-api.de/api/interpreter" -Method Post -Body @{data = $q} -UseBasicParsing -UserAgent "getawayguide-citymap/1.0" -TimeoutSec 300 -OutFile $out
      $sz = [math]::Round((Get-Item $out).Length / 1MB, 1); Write-Output "OK $slug $sz MB (try $i)"; $ok = $true; break
    } catch { $m = $_.Exception.Message; Write-Output ("retry {0} {1}: {2}" -f $slug, $i, $m); Start-Sleep -Seconds 25 }
  }
  if (-not $ok) { Write-Output "FAIL $slug" }
  Start-Sleep -Seconds 2
}
Write-Output "DONE"
