$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\index.html"
$lines = [System.IO.File]::ReadAllLines($f, [System.Text.Encoding]::UTF8)
Write-Host "Total lines: $($lines.Length)"
Write-Host ""
Write-Host "=== HEAD (lines 1-20) ==="
for ($i = 0; $i -lt [Math]::Min(20, $lines.Length); $i++) {
    # Skip base64 lines
    if ($lines[$i].Length -gt 300) {
        Write-Host "[$($i+1)] [base64 data, $($lines[$i].Length) chars]"
    } else {
        Write-Host "[$($i+1)] $($lines[$i])"
    }
}

Write-Host ""
Write-Host "=== NON-BASE64 HTML LINES ==="
$htmlLines = @()
for ($i = 0; $i -lt $lines.Length; $i++) {
    $l = $lines[$i]
    if ($l.Length -le 2000 -and $l.Trim().Length -gt 0) {
        $htmlLines += "[$($i+1)] $l"
    }
}
$htmlLines | ForEach-Object { Write-Host $_ }
