# Fetch a city map's OSM data from Overpass into .tmp/<slug>_osm.json (the Bash tool has no network).
#   powershell -File tools/fetch_citymap_osm.ps1 <slug> [S,W,N,E]
# Without a bbox, the config's own generous box is used (python tools/city_map.py <cfg> --bbox).
# Tries the mirrors in turn: overpass-api.de closes the connection from this box some evenings.
param([Parameter(Mandatory=$true)][string]$Slug, [string]$Bbox = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
if (-not $Bbox) { $Bbox = (& python tools/city_map.py "tools/city_maps/$Slug.json" --bbox).Trim() }
$q = "[out:json][timeout:240];`n(`n  way[`"highway`"]($Bbox);`n  way[`"natural`"=`"water`"]($Bbox);`n  way[`"waterway`"]($Bbox);`n  way[`"natural`"=`"coastline`"]($Bbox);`n  way[`"leisure`"~`"park|garden`"]($Bbox);`n  way[`"landuse`"=`"recreation_ground`"]($Bbox);`n  relation[`"natural`"=`"water`"]($Bbox);`n);`nout geom;"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
$mirrors = @("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter", "https://lz4.overpass-api.de/api/interpreter")
$r = $null
foreach ($m in $mirrors) {
  try {
    $r = Invoke-WebRequest -Uri $m -Method Post -Body @{ data = $q } -UserAgent "getawayguide-citymap/1.0 (kevindphan@gmail.com)" -TimeoutSec 300 -UseBasicParsing
    if ($r.StatusCode -eq 200 -and $r.Content.Length -gt 1000) { "mirror: $m"; break } else { $r = $null }
  } catch { "$m failed: $($_.Exception.Message)"; $r = $null }
}
if (-not $r) { throw "every Overpass mirror failed" }
$out = ".tmp/$Slug" + "_osm.json"
[IO.File]::WriteAllText((Join-Path $root $out), $r.Content, (New-Object System.Text.UTF8Encoding $false))
"fetched $Bbox -> $out ($($r.Content.Length) bytes)"
