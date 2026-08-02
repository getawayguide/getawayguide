# Bounded geocoder: resolves each POI by searching its bare NAME inside a viewbox
# around its city, which finds small venues (bars, hostels, belfries) that a flat
# "<name> <city> <country>" query misses entirely.
#
# Input : .tmp/geotsv.txt  -- key<TAB>name<TAB>city<TAB>country   (from .tmp/geotsv.py)
# Output: .tmp/geo.json    -- merged {key: [lat, lon]}, saved incrementally
$ProgressPreference = 'SilentlyContinue'
$ua = @{ "User-Agent" = "getawayguide-citymap/1.0 (kevindphan@gmail.com)" }
$geoPath = ".tmp\geo.json"
$geo = @{}
if (Test-Path $geoPath) {
  $obj = Get-Content $geoPath -Raw | ConvertFrom-Json
  foreach ($p in $obj.PSObject.Properties) { $geo[$p.Name] = $p.Value }
}
$centers = @{}
function Save-Geo { $geo | ConvertTo-Json -Compress | Out-File $geoPath -Encoding utf8 }

function Get-Nominatim([string]$url) {
  try { return Invoke-RestMethod -Uri $url -Headers $ua -TimeoutSec 25 } catch { return $null }
  finally { Start-Sleep -Milliseconds 1150 }
}

$rows = Get-Content ".tmp\geotsv.txt" -Encoding utf8
$done = 0; $hit = 0; $n = 0
foreach ($row in $rows) {
  if ($row.Trim() -eq "") { continue }
  $f = $row -split "`t"
  $key = $f[0]; $name = $f[1]; $city = $f[2]; $country = $f[3]
  $n++
  if ($geo.ContainsKey($key)) { $hit++; continue }

  # city centre (cached) -> viewbox for the bounded search
  $ckey = "$city|$country"
  if (-not $centers.ContainsKey($ckey)) {
    $cq = [uri]::EscapeDataString((($city -replace '\([^)]*\)', '') -replace '\s+', ' ').Trim() + ", " + $country)
    $cr = Get-Nominatim "https://nominatim.openstreetmap.org/search?q=$cq&format=json&limit=1"
    if ($cr -and $cr.Count -ge 1) { $centers[$ckey] = @([double]$cr[0].lat, [double]$cr[0].lon) }
    else { $centers[$ckey] = $null }
  }
  $c = $centers[$ckey]

  $clean = (($name -replace '\([^)]*\)', '') -replace '\s+', ' ').Trim()
  $enc = [uri]::EscapeDataString($clean)
  $r = $null
  if ($c) {                       # pass 1: bare name, bounded to ~25km around the city
    $d = 0.25
    $vb = "{0},{1},{2},{3}" -f ($c[1] - $d), ($c[0] + $d), ($c[1] + $d), ($c[0] - $d)
    $r = Get-Nominatim "https://nominatim.openstreetmap.org/search?q=$enc&format=json&limit=1&viewbox=$vb&bounded=1"
  }
  if (-not $r -or $r.Count -lt 1) {   # pass 2: name + city + country, unbounded
    $enc2 = [uri]::EscapeDataString("$clean, $city, $country")
    $r = Get-Nominatim "https://nominatim.openstreetmap.org/search?q=$enc2&format=json&limit=1"
  }
  if ($r -and $r.Count -ge 1) {
    $geo[$key] = @([double]$r[0].lat, [double]$r[0].lon)
    $hit++; Write-Output ("OK   {0}  {1},{2}" -f $key, $r[0].lat, $r[0].lon)
  } else {
    Write-Output ("MISS {0}" -f $key)
  }
  $done++
  if ($done % 20 -eq 0) { Save-Geo; Write-Output ("... saved ({0}/{1}, {2} resolved)" -f $n, $rows.Count, $hit) }
}
Save-Geo
Write-Output ("DONE {0}/{1} resolved; geo.json has {2} entries" -f $hit, $rows.Count, $geo.Count)
