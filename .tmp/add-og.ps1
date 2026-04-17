$pages = @{
  'index.html' = @{t='Getawayguide — Dispatches from the Road'; d='Independent travel writing covering Europe, the Americas, Asia, and beyond.'}
  'el-salvador.html' = @{t='El Salvador Travel Guide — Getawayguide'; d='Everything you need to know about El Salvador: volcanoes, beaches, Mayan ruins, and pupusas.'}
  'santa-ana.html' = @{t='The Ultimate Guide to Santa Ana, El Salvador — Getawayguide'; d='Volcano hikes, Lago de Coatepeque, Mayan ruins, pupusa classes and more.'}
  'destinations.html' = @{t='Destinations — Getawayguide'; d='Browse all travel guides by region.'}
  'about.html' = @{t='About — Getawayguide'; d='Independent travel writing from the road.'}
  'resources.html' = @{t='Resources — Getawayguide'; d='Travel tools, tips, and packing resources.'}
  'contact.html' = @{t='Contact — Getawayguide'; d='Get in touch.'}
  'privacy.html' = @{t='Privacy Policy — Getawayguide'; d='Privacy policy.'}
  'el-salvador-itinerary.html' = @{t='El Salvador 10-Day Itinerary — Getawayguide'; d='A 10-day itinerary for El Salvador.'}
}

foreach ($entry in $pages.GetEnumerator()) {
  $file = $entry.Key
  if (!(Test-Path $file)) { Write-Host "Skipped: $file"; continue }
  $c = [System.IO.File]::ReadAllText($file)
  if ($c -match 'og:type') { Write-Host "Already done: $file"; continue }
  $t = $entry.Value.t
  $d = $entry.Value.d
  $og = "<meta name=`"description`" content=`"$d`">`n<meta property=`"og:type`" content=`"website`">`n<meta property=`"og:site_name`" content=`"Getawayguide`">`n<meta property=`"og:title`" content=`"$t`">`n<meta property=`"og:description`" content=`"$d`">`n<meta property=`"og:image`" content=`"og-image.svg`">`n<meta name=`"twitter:card`" content=`"summary_large_image`">`n<meta name=`"twitter:image`" content=`"og-image.svg`">"
  $viewport = '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
  $c = $c.Replace($viewport, $viewport + "`n" + $og)
  [System.IO.File]::WriteAllText($file, $c)
  Write-Host "Done: $file"
}
