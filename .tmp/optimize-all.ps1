Set-Location "c:\Users\kevin\OneDrive\Documents\Travel Blog"

$pages = @(
    'about.html','contact.html','destinations.html',
    'el-salvador-itinerary.html','el-salvador-travel-guide.html',
    'el-salvador.html','el-tunco.html','posts.html','privacy.html',
    'resources.html','ruta-de-las-flores.html','santa-ana.html',
    '7-waterfalls-hike.html'
)

foreach ($page in $pages) {
    $c = [System.IO.File]::ReadAllText($page, [System.Text.Encoding]::UTF8)
    $orig = $c
    $fixes = @()

    # 1. Remove duplicate broken mobile nav drawer (pages with 2 HTML drawers)
    #    The broken one has href="#" href="..." double attributes
    $dupMatch = [regex]::Match($c,
        '<!-- MOBILE NAV DRAWER -->\r?\n<div class="mobile-nav-drawer"[^>]*>.*?</div>\r?\n\r?\n(?=<!-- MOBILE NAV DRAWER -->)',
        [System.Text.RegularExpressions.RegexOptions]::Singleline)
    if ($dupMatch.Success) {
        $c = $c.Remove($dupMatch.Index, $dupMatch.Length)
        $fixes += "removed duplicate broken mobile nav drawer"
    }

    # 2. Remove closeMobileMenu function (never called)
    $closePattern = [regex]::new('function closeMobileMenu\(\)\{[^}]+\}\r?\n?', [System.Text.RegularExpressions.RegexOptions]::Singleline)
    if ($closePattern.IsMatch($c)) {
        $c = $closePattern.Replace($c, '')
        $fixes += "removed unused closeMobileMenu()"
    }

    # 3. Fix corrupted copyright in footer (? or ï¿½ instead of &copy;)
    if ($c -match 'footer-copy">[^&<]*2026') {
        $c = [regex]::Replace($c, '<div class="footer-copy">[^<]*2026 getawayguide</div>', '<div class="footer-copy">&copy; 2026 getawayguide</div>')
        $fixes += "fixed footer copyright"
    }

    # 4. Remove trailing space before > in nav-logo, hero-cta, section-link
    foreach ($cls in @('nav-logo','hero-cta','section-link')) {
        if ($c.Contains("class=""$cls"" >")) {
            $c = $c.Replace("class=""$cls"" >", "class=""$cls"">")
            $fixes += "fixed trailing space in $cls"
        }
    }

    # 5. Fix footer links with trailing spaces (contact, privacy)
    $c = $c.Replace('<a href="contact.html" >', '<a href="contact.html">')
    $c = $c.Replace('<a href="privacy.html" >', '<a href="privacy.html">')

    if ($c -ne $orig) {
        [System.IO.File]::WriteAllText($page, $c, [System.Text.Encoding]::UTF8)
        Write-Host "${page}: $($fixes -join ', ')"
    } else {
        Write-Host "${page}: no changes needed"
    }
}

Write-Host ""
Write-Host "Done. Verifying..."
Write-Host ""

# Verify no closeMobileMenu remains
$remaining = Select-String -Path "*.html" -Pattern "closeMobileMenu" -SimpleMatch
if ($remaining) {
    Write-Host "WARN: closeMobileMenu still in: $($remaining.Filename -join ', ')"
} else {
    Write-Host "OK: no closeMobileMenu remaining"
}

# Verify no duplicate drawers
$dups = @()
foreach ($page in $pages) {
    $c = [System.IO.File]::ReadAllText($page, [System.Text.Encoding]::UTF8)
    $count = ([regex]::Matches($c, '<div class="mobile-nav-drawer"')).Count
    if ($count -gt 1) { $dups += "$page ($count)" }
}
if ($dups) { Write-Host "WARN: still has multiple drawers: $($dups -join ', ')" }
else { Write-Host "OK: no duplicate drawers" }
