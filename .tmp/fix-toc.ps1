$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\el-salvador-itinerary.html"
$c = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)

# 1. Insert static TOC before first h2 (when-to-visit)
$tocHtml = @'
    <div id="toc-b">
      <div class="toc-label">In this guide</div>
      <div class="toc-title">El Salvador Travel Guide with Perfect 1&#x2013;2 Week Itinerary</div>
      <div class="toc-cols">
        <div class="toc-col">
          <div class="toc-row"><span class="toc-num">01</span><a href="#overview" class="toc-link">Overview</a></div>
          <div class="toc-row"><span class="toc-num">02</span><a href="#when-to-visit" class="toc-link">When to Visit</a></div>
          <div class="toc-row"><span class="toc-num">03</span><a href="#places-to-visit" class="toc-link">Places to Visit</a></div>
          <div class="toc-row" style="flex-direction:column;align-items:stretch;gap:0">
            <div class="toc-section-head">
              <span class="toc-num">04</span>
              <a href="#the-route" class="toc-section-link">The Route</a>
            </div>
            <div class="toc-sub">
              <a href="#san-salvador">San Salvador</a>
              <a href="#santa-ana">Santa Ana</a>
              <a href="#ruta-de-las-flores">Ruta de las Flores</a>
              <a href="#el-tunco">El Tunco &amp; The Coast</a>
              <a href="#suchitoto">Suchitoto</a>
            </div>
          </div>
          <div class="toc-row"><span class="toc-num">05</span><a href="#one-week" class="toc-link">1-Week Itinerary</a></div>
          <div class="toc-row"><span class="toc-num">06</span><a href="#two-weeks" class="toc-link">2-Week Itinerary</a></div>
        </div>
        <div class="toc-col">
          <div class="toc-row"><span class="toc-num">07</span><a href="#getting-around" class="toc-link">Getting Around</a></div>
          <div class="toc-row"><span class="toc-num">08</span><a href="#getting-there" class="toc-link">Getting There</a></div>
          <div class="toc-row"><span class="toc-num">09</span><a href="#budget" class="toc-link">How Much Does It Cost</a></div>
          <div class="toc-row"><span class="toc-num">10</span><a href="#things-to-do" class="toc-link">Top Things to Do</a></div>
          <div class="toc-row"><span class="toc-num">11</span><a href="#safety" class="toc-link">Safety</a></div>
          <div class="toc-row"><span class="toc-num">12</span><a href="#basic-facts" class="toc-link">Basic Facts</a></div>
          <div class="toc-row"><span class="toc-num">13</span><a href="#practical-tips" class="toc-link">Practical Tips &amp; Budget</a></div>
        </div>
      </div>
    </div>
'@

$firstH2 = '    <h2 class="article-h2" id="when-to-visit"'
if ($c.Contains($firstH2)) {
    $c = $c.Replace($firstH2, $tocHtml + $firstH2)
    Write-Host "OK: inserted static TOC before first h2"
} else {
    Write-Host "WARN: first h2 anchor not found"
}

# 2. Remove the old <style> block (lines 185-230 in original)
$styleBlock = [regex]::Match($c, '<style>\s*@media\(max-width:768px\)\{[^<]*#page-article-sv-itinerary.*?</style>', [System.Text.RegularExpressions.RegexOptions]::Singleline)
if ($styleBlock.Success) {
    $c = $c.Remove($styleBlock.Index, $styleBlock.Length)
    Write-Host "OK: removed old inline style block"
} else {
    Write-Host "WARN: inline style block not matched"
}

# 3. Remove the old <script> block (TOC JS generator)
$scriptBlock = [regex]::Match($c, '<script>\s*\(function\(\) \{.*?toc-arrow.*?\}\)\(\);\s*</script>', [System.Text.RegularExpressions.RegexOptions]::Singleline)
if ($scriptBlock.Success) {
    $c = $c.Remove($scriptBlock.Index, $scriptBlock.Length)
    Write-Host "OK: removed old TOC script block"
} else {
    Write-Host "WARN: TOC script block not matched"
}

# 4. Fix footer copyright if still broken
$c = [regex]::Replace($c, '<div class="footer-copy">[^<]*2026 getawayguide</div>', '<div class="footer-copy">&copy; 2026 getawayguide</div>')

[System.IO.File]::WriteAllText($f, $c, [System.Text.Encoding]::UTF8)
Write-Host "Saved"
