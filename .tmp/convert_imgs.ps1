function GetAttr2([string]$tag, [string]$name) {
    $dq = [char]34  # double-quote character
    $k1 = ' ' + $name + '=' + $dq
    $k2 = $name + '=' + $dq
    $patterns = @($k1, $k2)
    foreach ($key in $patterns) {
        $start = $tag.IndexOf($key)
        if ($start -ge 0) {
            $start += $key.Length
            $end = $tag.IndexOf($dq, $start)
            if ($end -ge 0) { return $tag.Substring($start, $end - $start) }
        }
    }
    return ''
}
function GetStyleProp2([string]$style, [string]$prop) {
    $key = $prop + ':'
    $start = $style.IndexOf($key)
    if ($start -lt 0) { return '' }
    $start += $key.Length
    while ($start -lt $style.Length -and $style[$start] -eq ' ') { $start++ }
    $end = $style.IndexOf(';', $start)
    if ($end -lt 0) { $end = $style.Length }
    return $style.Substring($start, $end - $start).Trim()
}
function ConvertFile2([string]$filePath) {
    $dq = [char]34
    $bytes = [System.IO.File]::ReadAllBytes($filePath)
    $content = [System.Text.Encoding]::UTF8.GetString($bytes)
    $sb = New-Object System.Text.StringBuilder($content.Length + 5000)
    $pos = 0; $count = 0
    while ($pos -lt $content.Length) {
        $i = $content.IndexOf('<img ', $pos)
        if ($i -lt 0) { [void]$sb.Append($content.Substring($pos)); break }
        $j = $content.IndexOf('>', $i)
        if ($j -lt 0) { [void]$sb.Append($content.Substring($pos)); break }
        $tag = $content.Substring($i, $j - $i + 1)
        $style = GetAttr2 $tag 'style'
        if ($style.Length -gt 0 -and $style.Contains('aspect-ratio') -and $style.Contains('object-fit') -and (-not $style.Contains('position:absolute')) -and (-not $style.Contains('position: absolute'))) {
            $src = GetAttr2 $tag 'src'
            $alt = GetAttr2 $tag 'alt'
            $ratio = GetStyleProp2 $style 'aspect-ratio'
            $width = GetStyleProp2 $style 'width'
            if (-not $width) { $width = '100%' }
            $br = GetStyleProp2 $style 'border-radius'
            if (-not $br) { $br = '2px' }
            $margin = GetStyleProp2 $style 'margin'
            $mw = GetStyleProp2 $style 'max-width'
            $objpos = GetStyleProp2 $style 'object-position'
            $mwPart = if ($mw -and $mw -ne '100%') { 'max-width:' + $mw + ';' } else { '' }
            $marginPart = if ($margin) { 'margin:' + $margin + ';' } else { '' }
            $posPart = if ($objpos -and $objpos -ne 'center center') { 'object-position:' + $objpos + ';' } else { '' }
            $wrapper = '<span style=' + $dq + 'display:block;position:relative;overflow:hidden;width:' + $width + ';' + $mwPart + 'aspect-ratio:' + $ratio + ';border-radius:' + $br + ';' + $marginPart + $dq + '><img src=' + $dq + $src + $dq + ' alt=' + $dq + $alt + $dq + ' style=' + $dq + 'position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;' + $posPart + $dq + '></span>'
            [void]$sb.Append($content.Substring($pos, $i - $pos))
            [void]$sb.Append($wrapper)
            $count++
        } else {
            [void]$sb.Append($content.Substring($pos, $j - $pos + 1))
        }
        $pos = $j + 1
    }
    $newContent = $sb.ToString()
    if ($newContent.Length -gt 1000 -and $count -gt 0) {
        [System.IO.File]::WriteAllBytes($filePath, [System.Text.Encoding]::UTF8.GetBytes($newContent))
        Write-Host ($filePath.Split('\')[-1] + ': converted ' + $count + ' imgs OK, length=' + $newContent.Length)
    } else {
        Write-Host ($filePath.Split('\')[-1] + ': count=' + $count + ' length=' + $newContent.Length)
    }
}
ConvertFile2 'c:\Users\kevin\OneDrive\Documents\Travel Blog\santa-ana.html'
ConvertFile2 'c:\Users\kevin\OneDrive\Documents\Travel Blog\ruta-de-las-flores.html'
