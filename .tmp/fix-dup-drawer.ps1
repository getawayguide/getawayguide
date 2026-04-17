$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\index.html"
$c = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)

# Remove the first (broken) mobile nav drawer block including its comment
$bad = [regex]::Match($c, '<!-- MOBILE NAV DRAWER -->\r?\n<div class="mobile-nav-drawer".*?</div>\r?\n\r?\n(?=<!-- MOBILE NAV DRAWER -->)', [System.Text.RegularExpressions.RegexOptions]::Singleline)
if ($bad.Success) {
    $c = $c.Remove($bad.Index, $bad.Length)
    [System.IO.File]::WriteAllText($f, $c, [System.Text.Encoding]::UTF8)
    Write-Host "Removed duplicate mobile nav drawer ($($bad.Length) chars)"
} else {
    Write-Host "Pattern not matched - trying line-based approach"
    $lines = $c -split "`r`n"
    if ($lines.Count -eq 1) { $lines = $c -split "`n" }

    # Find first occurrence of mobile-nav-drawer comment and remove through closing </div>
    $start = -1
    $end = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($start -eq -1 -and $lines[$i] -match '<!-- MOBILE NAV DRAWER -->') {
            $start = $i
        } elseif ($start -ge 0 -and $end -eq -1 -and $lines[$i] -eq '</div>') {
            $end = $i
            break
        }
    }
    if ($start -ge 0 -and $end -ge 0) {
        Write-Host "Found block: lines $($start+1) to $($end+1)"
        $newLines = $lines[0..($start-1)] + $lines[($end+1)..($lines.Count-1)]
        $result = $newLines -join "`n"
        [System.IO.File]::WriteAllText($f, $result, [System.Text.Encoding]::UTF8)
        Write-Host "Removed duplicate block"
    } else {
        Write-Host "Could not locate block"
    }
}
