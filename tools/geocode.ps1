param([string]$cc = "")
$ProgressPreference = 'SilentlyContinue'
$ua = @{ "User-Agent" = "getawayguide-citymap/1.0 (kevindphan@gmail.com)" }
$geoPath = ".tmp\geo.json"
$geo = @{}
if (Test-Path $geoPath) {
  $obj = Get-Content $geoPath -Raw | ConvertFrom-Json
  foreach ($p in $obj.PSObject.Properties) { $geo[$p.Name] = $p.Value }
}
$ccq = ""
if ($cc -ne "") { $ccq = "&countrycodes=$cc" }
foreach ($q in Get-Content ".tmp\geoq.txt" -Encoding utf8) {
  $q = $q.Trim()
  if ($q -eq "" -or $geo.ContainsKey($q)) { continue }
  $search = ($q -replace '\([^)]*\)', '' -replace '\s+', ' ').Trim()   # drop parentheticals for the query
  $enc = [uri]::EscapeDataString($search)
  try {
    $r = Invoke-RestMethod -Uri "https://nominatim.openstreetmap.org/search?q=$enc&format=json&limit=1$ccq" -Headers $ua -TimeoutSec 25
    if ($r.Count -ge 1) { $geo[$q] = @([double]$r[0].lat, [double]$r[0].lon); Write-Output ("OK  {0}  {1},{2}" -f $q, $r[0].lat, $r[0].lon) }
    else { Write-Output ("MISS {0}" -f $q) }
  } catch { Write-Output ("ERR {0}: {1}" -f $q, $_.Exception.Message) }
  Start-Sleep -Milliseconds 1200
}
$geo | ConvertTo-Json -Compress | Out-File $geoPath -Encoding utf8
Write-Output ("--- geo.json now has {0} entries" -f $geo.Count)
