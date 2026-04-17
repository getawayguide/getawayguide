$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\el-salvador-itinerary.html"
$lines = [System.IO.File]::ReadAllLines($f, [System.Text.Encoding]::UTF8)
for ($i = 90; $i -lt $lines.Length; $i++) {
    $l = $lines[$i]
    if ($l.Length -gt 300) { $l = $l.Substring(0,300) + "..." }
    Write-Host "[$($i+1)] $l"
}
