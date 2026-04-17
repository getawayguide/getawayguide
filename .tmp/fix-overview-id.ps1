$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\el-salvador-itinerary.html"
$c = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)

$lines = $c -split "`n"
$line = $lines[93]
Write-Host "Line 94 tag: $($line.Substring(0, [Math]::Min(50, $line.Length)))"
Write-Host "Has id=overview: $($line.Contains('id=""overview""'))"
Write-Host "Has best kept: $($line.Contains('best kept secrets'))"
Write-Host "Is <p>: $($line.TrimStart().StartsWith('<p'))"

# Add id="overview" to the intro paragraph (line 94) regardless of content
if ($line.TrimStart().StartsWith('<p>') -and -not $line.Contains('id=')) {
    $lines[93] = $line.Replace('<p>', '<p id="overview">')
    $result = $lines -join "`n"
    [System.IO.File]::WriteAllText($f, $result, [System.Text.Encoding]::UTF8)
    Write-Host "Added id=overview to intro paragraph"
} elseif ($line.Contains('id="overview"')) {
    Write-Host "Already has id=overview"
} else {
    Write-Host "Line 94 does not start with <p> or already has id"
}
