$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\el-salvador-itinerary.html"
$c = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)

$tilesHtml = @'

    <div class="essentials-grid">
      <div class="essentials-card">
        <div class="essentials-head"><span class="essentials-icon">&#9728;&#65039;</span><span class="essentials-title">Best Time to Go</span></div>
        <p class="essentials-body">Dry season (November to April) is peak season and most recommended; visit in the off-season for fewer tourists and lusher scenery.</p>
      </div>
      <div class="essentials-card">
        <div class="essentials-head"><span class="essentials-icon">&#128197;</span><span class="essentials-title">How Long?</span></div>
        <p class="essentials-body">1 week to cover the highlights and 2 weeks for a slower pace to explore thoroughly.</p>
      </div>
      <div class="essentials-card">
        <div class="essentials-head"><span class="essentials-icon">&#128181;</span><span class="essentials-title">Daily Budget</span></div>
        <p class="essentials-body">$30 to $50 for budget backpacking and $60 to $90 for mid-range options.</p>
      </div>
      <div class="essentials-card">
        <div class="essentials-head"><span class="essentials-icon">&#9992;&#65039;</span><span class="essentials-title">Getting There</span></div>
        <p class="essentials-body">Fly into El Salvador International Airport (SAL) or tourist shuttle from Guatemala, Nicaragua or Honduras.</p>
      </div>
      <div class="essentials-card">
        <div class="essentials-head"><span class="essentials-icon">&#128652;</span><span class="essentials-title">Getting Around</span></div>
        <p class="essentials-body">Rent a car for flexibility, use Uber, or hop on the extensive network of chicken buses ($0.50 to $2 for most rides).</p>
      </div>
      <div class="essentials-card">
        <div class="essentials-head"><span class="essentials-icon">&#128706;</span><span class="essentials-title">Visa Requirements</span></div>
        <p class="essentials-body">Visa-free for 90 days for US, EU, UK and Australia. Shares a 90-day CA-4 visa agreement with Guatemala, Honduras, and Nicaragua.</p>
      </div>
      <div class="essentials-card">
        <div class="essentials-head"><span class="essentials-icon">&#128176;</span><span class="essentials-title">Currency</span></div>
        <p class="essentials-body">Uses the U.S. Dollar (and Bitcoin is widely accepted!). ATMs are widely available and credit cards accepted at major tourist sites.</p>
      </div>
      <div class="essentials-card">
        <div class="essentials-head"><span class="essentials-icon">&#127970;</span><span class="essentials-title">Accommodation</span></div>
        <p class="essentials-body">Hostels, hotels and Airbnbs are widespread in all major tourist destinations.</p>
      </div>
    </div>
'@

# Insert after the image grid closing </div> and before the when-to-visit h2
$marker = '    </div>

    <h2 class="article-h2" id="when-to-visit"'
$replacement = "    </div>$tilesHtml
    <h2 class=""article-h2"" id=""when-to-visit"""

if ($c.Contains($marker)) {
    [System.IO.File]::WriteAllText($f, $c.Replace($marker, $replacement), [System.Text.Encoding]::UTF8)
    Write-Host "OK: tiles inserted"
} else {
    Write-Host "Marker not found - trying CRLF variant"
    $marker2 = "    </div>`r`n`r`n    <h2 class=""article-h2"" id=""when-to-visit"""
    if ($c.Contains($marker2)) {
        [System.IO.File]::WriteAllText($f, $c.Replace($marker2, "    </div>$tilesHtml`r`n    <h2 class=""article-h2"" id=""when-to-visit"""), [System.Text.Encoding]::UTF8)
        Write-Host "OK: tiles inserted (CRLF)"
    } else {
        Write-Host "FAIL: could not find insertion point"
    }
}
