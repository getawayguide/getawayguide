$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\el-salvador-itinerary.html"
$c = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)
$lines = $c -split "`n"

# Print lines 80-100 and 175-245 to see structure around article body and TOC script
Write-Host "=== Lines 80-100 ==="
for ($i = 79; $i -lt 100 -and $i -lt $lines.Length; $i++) {
    $l = $lines[$i]
    if ($l.Length -gt 120) { $l = $l.Substring(0,120) + "..." }
    Write-Host "[$($i+1)] $l"
}

Write-Host ""
Write-Host "=== Lines 175-240 ==="
for ($i = 174; $i -lt 240 -and $i -lt $lines.Length; $i++) {
    $l = $lines[$i]
    if ($l.Length -gt 120) { $l = $l.Substring(0,120) + "..." }
    Write-Host "[$($i+1)] $l"
}
