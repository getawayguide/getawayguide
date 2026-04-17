$root = "C:\Users\kevin\OneDrive\Documents\Travel Blog"
$files = Get-ChildItem -Path $root -Filter "*.html" -File

# Map: old filename -> clean URL
$pages = [ordered]@{
  "index.html"                 = "/"
  "about.html"                 = "/about"
  "contact.html"               = "/contact"
  "destinations.html"          = "/destinations"
  "el-salvador.html"           = "/el-salvador"
  "el-salvador-itinerary.html" = "/el-salvador-itinerary"
  "santa-ana.html"             = "/santa-ana"
  "ruta-de-las-flores.html"    = "/ruta-de-las-flores"
  "resources.html"             = "/resources"
  "privacy.html"               = "/privacy"
  "posts.html"                 = "/posts"
  "editor.html"                = "/editor"
}

foreach ($file in $files) {
  $c = [System.IO.File]::ReadAllText($file.FullName)

  # Add empty Jekyll front matter to all pages except index.html
  # so Jekyll applies the permalink: /:basename rule
  if ($file.Name -ne "index.html" -and -not $c.StartsWith("---")) {
    $c = "---`n---`n" + $c
  }

  # Replace href and location.href for each page
  foreach ($entry in $pages.GetEnumerator()) {
    $old = $entry.Key
    $new = $entry.Value

    # href="filename.html" (no anchor)
    $c = $c.Replace("href=`"$old`"", "href=`"$new`"")
    # href="filename.html#anchor"
    $c = $c.Replace("href=`"$old#", "href=`"$new#")
    # href='filename.html'
    $c = $c.Replace("href='$old'", "href='$new'")
    # href='filename.html#anchor'
    $c = $c.Replace("href='$old#", "href='$new#")
    # location.href='filename.html'
    $c = $c.Replace("location.href='$old'", "location.href='$new'")
    # onclick="location.href='filename.html'"
    $c = $c.Replace("onclick=`"location.href='$old'`"", "onclick=`"location.href='$new'`"")
  }

  # Update nav active-state JS: match clean path segments instead of filenames
  $c = $c.Replace(
    "window.location.pathname.split('/').pop()||'index.html'",
    "window.location.pathname.split('/').filter(Boolean).pop()||''"
  )
  $c = $c.Replace(
    "'index.html':'nav-home','':'nav-home','destinations.html':'nav-destinations','resources.html':'nav-resources','about.html':'nav-about'",
    "'':'nav-home','destinations':'nav-destinations','resources':'nav-resources','about':'nav-about'"
  )

  [System.IO.File]::WriteAllText($file.FullName, $c)
  Write-Host "Done: $($file.Name)"
}

Write-Host "All done."
