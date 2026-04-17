Add-Type -AssemblyName System.Drawing

$root = 'c:\Users\kevin\OneDrive\Documents\Travel Blog'
$maxDim = 1600
$quality = 90L
$created = 0; $skipped = 0

$enc = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
$jpegParams = New-Object System.Drawing.Imaging.EncoderParameters(1)
$jpegParams.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, $quality)

# Find all unique image paths referenced in HTML files
$imgs = @{}
Get-ChildItem "$root\*.html" | ForEach-Object {
    $content = Get-Content $_.FullName -Raw
    [regex]::Matches($content, 'src="(Images/[^"]+\.(jpe?g|JPEG|JPG|png|PNG))"') | ForEach-Object {
        $imgs[$_.Groups[1].Value] = 1
    }
}
Write-Host "Found $($imgs.Count) unique image references"

foreach ($srcRelPath in $imgs.Keys) {
    $srcFull = Join-Path $root $srcRelPath
    if (-not (Test-Path $srcFull)) { Write-Host "MISSING: $srcRelPath"; continue }

    $webRelPath = $srcRelPath -replace '^Images/', 'Images/web/'
    $webFull = Join-Path $root $webRelPath

    if (Test-Path $webFull) { $skipped++; continue }

    $dir = Split-Path $webFull
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    $ext = [System.IO.Path]::GetExtension($srcRelPath).ToLower()

    if ($ext -eq '.png') {
        Copy-Item $srcFull $webFull
        $created++
        Write-Host "PNG copy: $([System.IO.Path]::GetFileName($srcRelPath))"
        continue
    }

    try {
        $img = [System.Drawing.Image]::FromFile($srcFull)
        $w = $img.Width; $h = $img.Height

        if ($w -le $maxDim -and $h -le $maxDim) {
            $img.Dispose()
            Copy-Item $srcFull $webFull
            $skipped++
            continue
        }

        if ($w -ge $h) { $newW = $maxDim; $newH = [int]([math]::Round($h * $maxDim / $w)) }
        else            { $newH = $maxDim; $newW = [int]([math]::Round($w * $maxDim / $h)) }

        $bmp = New-Object System.Drawing.Bitmap($newW, $newH)
        $g   = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode    = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.PixelOffsetMode      = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $g.CompositingQuality   = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
        $g.DrawImage($img, 0, 0, $newW, $newH)
        $img.Dispose()

        $bmp.Save($webFull, $enc, $jpegParams)
        $bmp.Dispose(); $g.Dispose()
        $created++
        Write-Host "Created: $([System.IO.Path]::GetFileName($webFull)) ${newW}x${newH}"
    } catch {
        try { $img.Dispose() } catch {}
        Write-Host "ERROR: $srcRelPath - $($_.Exception.Message)"
    }
}
Write-Host "Done: $created created, $skipped skipped/copied"
