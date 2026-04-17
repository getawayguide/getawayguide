$files = @('index.html','destinations.html','resources.html','about.html','contact.html','el-salvador.html','el-salvador-itinerary.html','privacy.html','santa-ana.html')
$dir = 'c:\Users\kevin\OneDrive\Documents\Travel Blog'

$newFontLink = '<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,400;0,9..144,700;1,9..144,300;1,9..144,400&family=Lora:ital,wght@0,400;0,600;1,400&family=Source+Serif+4:ital,opsz,wght@0,8..60,300;0,8..60,400;1,8..60,300&family=Space+Mono:ital,wght@0,400;1,400&family=Jost:ital,wght@0,300;0,400;1,300&display=swap" rel="stylesheet">'

$themeRestore = '<script>try{var t=localStorage.getItem(''gag-theme'');if(t)document.documentElement.classList.add(t)}catch(e){}</script>'

$switcherScript = '<script src="theme-switcher.js"></script>'

foreach ($f in $files) {
  $path = Join-Path $dir $f
  if (-not (Test-Path $path)) { Write-Host "MISSING: $f"; continue }
  $content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)

  # Replace font link (the long messy one)
  $content = [regex]::Replace($content, '<link href="https://fonts\.googleapis\.com/css2\?[^"]*" rel="stylesheet">', $newFontLink)

  # Add theme restore before styles.css link if not already there
  if ($content -notmatch 'gag-theme') {
    $content = $content -replace '<link rel="stylesheet" href="styles\.css">', ($themeRestore + "`n" + '<link rel="stylesheet" href="styles.css">')
  }

  # Add switcher before </body> if not already there
  if ($content -notmatch 'theme-switcher\.js') {
    $content = $content -replace '</body>', ($switcherScript + "`n</body>")
  }

  [System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)
  Write-Host "Updated: $f"
}
Write-Host "Done."
