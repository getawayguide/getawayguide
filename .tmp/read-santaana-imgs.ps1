$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\santa-ana.html"
$lines = [System.IO.File]::ReadAllLines($f, [System.Text.Encoding]::UTF8)
Write-Host "Line 145:"
Write-Host $lines[144]
Write-Host ""
Write-Host "Line 179:"
Write-Host $lines[178]
