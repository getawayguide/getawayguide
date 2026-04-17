$f = "c:\Users\kevin\OneDrive\Documents\Travel Blog\el-salvador-itinerary.html"
$c = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)

# Use regex to replace the entire styled h2 block containing this text
$pattern = '<h2 class="article-h2" id="overview">.*?</h2>'
$replacement = '<p>El Salvador is one of Central America''s best kept secrets. It''s tiny — you can drive coast to coast in under three hours — but it packs in volcanoes, surf beaches, colonial towns, and cloud forest all within reach of each other. Here''s how I''d spend one to two weeks if I were doing it again.</p>'

$result = [regex]::Replace($c, $pattern, $replacement, [System.Text.RegularExpressions.RegexOptions]::Singleline)

if ($result -ne $c) {
    [System.IO.File]::WriteAllText($f, $result, [System.Text.Encoding]::UTF8)
    Write-Host "Fixed"
} else {
    Write-Host "No match"
}
