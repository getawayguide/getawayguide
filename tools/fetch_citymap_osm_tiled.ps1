# Tiled Overpass fetch for dense cities (one request for Bangkok 504s): split the slug's bbox into
# N x N tiles, fetch each with retries, save .tmp/<slug>_osm_tile_<i>_<j>.json; merge with
# tools/merge_osm_tiles.py afterwards. Usage: tools/fetch_citymap_osm_tiled.ps1 <slug> <N>
param([string]$slug, [int]$N = 3)
Set-Location "C:\Users\kevin\OneDrive\Documents\Travel Blog"
$bboxText = (& python tools/city_map.py "tools/city_maps/$slug.json" --bbox | Select-Object -Last 1).Trim()
$b = $bboxText.Split(",") | ForEach-Object { [double]$_ }
$S, $W, $Nn, $E = $b
$mirrors = @("https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
for ($i = 0; $i -lt $N; $i++) { for ($j = 0; $j -lt $N; $j++) {
  $out = ".tmp\$($slug)_osm_tile_$($i)_$($j).json"
  if ((Test-Path $out) -and ((Get-Item $out).Length -gt 1000)) { continue }
  $s1 = $S + ($Nn - $S) * $i / $N; $n1 = $S + ($Nn - $S) * ($i + 1) / $N
  $w1 = $W + ($E - $W) * $j / $N; $e1 = $W + ($E - $W) * ($j + 1) / $N
  $bbox = "{0:F5},{1:F5},{2:F5},{3:F5}" -f $s1, $w1, $n1, $e1
  $q = @"
[out:json][timeout:240];
(
  way["highway"]($bbox);
  way["natural"="water"]($bbox);
  way["waterway"]($bbox);
  way["natural"="coastline"]($bbox);
  way["leisure"~"park|garden"]($bbox);
  way["landuse"="recreation_ground"]($bbox);
  relation["natural"="water"]($bbox);
);
out geom;
"@
  $ok = $false
  for ($try = 0; $try -lt 6 -and -not $ok; $try++) {
    $url = $mirrors[$try % 2]
    try {
      $r = Invoke-WebRequest -Uri $url -Method Post -Body @{data=$q} -UserAgent "getawayguide-citymap/1.0 (kevindphan@gmail.com)" -TimeoutSec 300 -UseBasicParsing
      $r.Content | Out-File -Encoding utf8 $out
      Write-Output ("{0} tile {1},{2} ok ({3:N1} MB)" -f $slug, $i, $j, ((Get-Item $out).Length / 1MB))
      $ok = $true
    } catch {
      Write-Output "$slug tile $i,$j try $try failed: $($_.Exception.Message)"
      Start-Sleep -Seconds (10 * ($try + 1))
    }
  }
} }
