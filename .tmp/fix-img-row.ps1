$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\el-salvador-itinerary.html"
$c = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)

$old = '<div class="article-img-row">
      <div class="article-img-col"><img src="Images/El Salvador/Santa Ana/hectors-tour-2.jpg" alt="Santa Ana volcano tour, El Salvador"></div>
      <div class="article-img-col"><img src="Images/El Salvador/La Ruta de las Flores/Waterfalls/Main Waterfall 3.JPEG" alt="Waterfall, Ruta de las Flores, El Salvador"></div>
      <div class="article-img-col"><img src="Images/El Salvador/El Tunco and Taquillo/El Tunco/El Tunco Sunset 1.JPEG" alt="El Tunco sunset, El Salvador"></div>
    </div>'

$new = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin:1.5rem 0 1.75rem;align-items:start">
      <span style="display:block;position:relative;overflow:hidden;width:100%;aspect-ratio:4/5;border-radius:2px;"><img src="Images/El Salvador/Santa Ana/hectors-tour-2.jpg" alt="Santa Ana volcano tour" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;"></span>
      <span style="display:block;position:relative;overflow:hidden;width:100%;aspect-ratio:4/5;border-radius:2px;"><img src="Images/El Salvador/La Ruta de las Flores/Waterfalls/Main Waterfall 3.JPEG" alt="Waterfall, Ruta de las Flores" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;"></span>
      <span style="display:block;position:relative;overflow:hidden;width:100%;aspect-ratio:4/5;border-radius:2px;"><img src="Images/El Salvador/El Tunco and Taquillo/El Tunco/El Tunco Sunset 1.JPEG" alt="El Tunco sunset" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;"></span>
    </div>'

if ($c.Contains($old)) {
    [System.IO.File]::WriteAllText($f, $c.Replace($old, $new), [System.Text.Encoding]::UTF8)
    Write-Host "OK"
} else {
    Write-Host "Pattern not found"
}
