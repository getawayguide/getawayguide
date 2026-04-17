$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\index.html"
$c = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)
$orig = $c

# 1. Remove the duplicate/broken first mobile nav drawer block
$badDrawer = "<!-- MOBILE NAV DRAWER -->
<div class=""mobile-nav-drawer"" id=""mobile-nav-drawer"">
  <ul class=""mobile-nav-links"">
    <li><a href=""#"" href=""index.html"">Home</a></li>
    <li>
      <a href=""#"" href=""destinations.html"">Destinations</a>
      <div class=""mobile-nav-continents"">
        <a href=""#"" href=""destinations.html#europe"">Europe</a>
        <a href=""#"" href=""destinations.html#americas"">Americas</a>
        <a href=""#"" href=""destinations.html#asia"">Asia</a>
        <a href=""#"" href=""destinations.html#africa"">Africa</a>
        <a href=""#"" href=""destinations.html#oceania"">Oceania</a>
      </div>
    </li>
    <li><a href=""#"" href=""resources.html"">Resources</a></li>
    <li><a href=""#"" href=""about.html"">About Me</a></li>
  </ul>
  <div class=""mobile-drawer-footer"">c 2026 getawayguide</div>
</div>"
if ($c.Contains($badDrawer)) {
    $c = $c.Replace($badDrawer, "")
    Write-Host "OK: removed duplicate broken mobile nav drawer"
} else { Write-Host "WARN: duplicate drawer not found" }

# 2. Remove leftover SPA comment
$c = $c.Replace("`n<!-- DESTINATIONS -->", "")
Write-Host "OK: removed leftover DESTINATIONS comment"

# 3. Fix footer copyright symbol
$c = $c.Replace(
    '<div class="footer-copy">c 2026 getawayguide</div>',
    '<div class="footer-copy">&copy; 2026 getawayguide</div>'
)
Write-Host "OK: fixed footer copyright"

# 4. Remove closeMobileMenu (defined but never called)
$closeFunc = "function closeMobileMenu(){
  var d=document.getElementById('mobile-nav-drawer'),b=document.getElementById('nav-hamburger');
  d.classList.remove('open');b.classList.remove('open');
  document.body.style.overflow='';
}"
if ($c.Contains($closeFunc)) {
    $c = $c.Replace($closeFunc + "`n", "")
    Write-Host "OK: removed unused closeMobileMenu function"
} else { Write-Host "WARN: closeMobileMenu pattern not matched" }

# 5. Remove extra blank lines in <head>
$c = $c.Replace("<link rel=""stylesheet"" href=""styles.css?v=7"">

`n
</head>", "<link rel=""stylesheet"" href=""styles.css?v=7"">
</head>")
Write-Host "OK: cleaned up head blank lines"

# 6. Fix trailing spaces in attributes
$c = $c.Replace('class="nav-logo" >', 'class="nav-logo">')
$c = $c.Replace('class="hero-cta" >', 'class="hero-cta">')
$c = $c.Replace('class="section-link" >', 'class="section-link">')
Write-Host "OK: removed trailing spaces from attributes"

if ($c -ne $orig) {
    [System.IO.File]::WriteAllText($f, $c, [System.Text.Encoding]::UTF8)
    Write-Host ""
    Write-Host "index.html updated"
} else {
    Write-Host "No changes made"
}
