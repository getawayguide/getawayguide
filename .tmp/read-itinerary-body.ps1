$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\el-salvador-itinerary.html"
$lines = [System.IO.File]::ReadAllLines($f, [System.Text.Encoding]::UTF8)
# Print lines 91-100 in full
for ($i = 90; $i -lt 105 -and $i -lt $lines.Length; $i++) {
    $l = $lines[$i]
    Write-Host "[$($i+1)] $l"
}
