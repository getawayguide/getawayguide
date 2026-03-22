# serve.ps1 — Simple local HTTP server for the travel blog
# Usage: right-click -> "Run with PowerShell"  OR  powershell -File serve.ps1

$port    = 8080
$root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$url     = "http://localhost:$port/"
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($url)
$listener.Start()

Write-Host ""
Write-Host "  Travel Blog running at: $url" -ForegroundColor Cyan
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

# Open browser automatically
Start-Process $url

$mimeTypes = @{
    '.html' = 'text/html; charset=utf-8'
    '.css'  = 'text/css; charset=utf-8'
    '.js'   = 'application/javascript'
    '.jpg'  = 'image/jpeg'
    '.jpeg' = 'image/jpeg'
    '.png'  = 'image/png'
    '.webp' = 'image/webp'
    '.svg'  = 'image/svg+xml'
    '.ico'  = 'image/x-icon'
    '.woff2'= 'font/woff2'
    '.woff' = 'font/woff'
}

try {
    while ($listener.IsListening) {
        $ctx  = $listener.GetContext()
        $req  = $ctx.Request
        $resp = $ctx.Response

        $localPath = $req.Url.LocalPath -replace '/', [System.IO.Path]::DirectorySeparatorChar
        if ($localPath -eq '\' -or $localPath -eq '/') { $localPath = '\index.html' }
        $file = Join-Path $root $localPath.TrimStart('\')

        if (Test-Path $file -PathType Leaf) {
            $ext  = [System.IO.Path]::GetExtension($file).ToLower()
            $mime = if ($mimeTypes.ContainsKey($ext)) { $mimeTypes[$ext] } else { 'application/octet-stream' }
            $bytes = [System.IO.File]::ReadAllBytes($file)
            $resp.ContentType   = $mime
            $resp.ContentLength64 = $bytes.Length
            $resp.OutputStream.Write($bytes, 0, $bytes.Length)
        } else {
            $resp.StatusCode = 404
            $msg = [System.Text.Encoding]::UTF8.GetBytes("404 Not Found: $($req.Url.LocalPath)")
            $resp.OutputStream.Write($msg, 0, $msg.Length)
        }
        $resp.OutputStream.Close()
    }
} finally {
    $listener.Stop()
}
