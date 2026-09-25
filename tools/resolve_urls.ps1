# Resolve a list of URLs to their final address and status, concurrently.
#
# Python on this box cannot reach the internet (its TLS stack is broken and the
# agent's Bash tool has no egress), but PowerShell can. Every tool here that
# needs the network goes through a script like this one: the caller writes a
# URL per line, we write a TSV back, and the Python side does the thinking.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools/resolve_urls.ps1 `
#              -InFile .tmp/urls.txt -OutFile .tmp/scan.tsv [-Workers 8]
#
# Output columns: url, status, final, note
#   status  the HTTP status of the FINAL response, or 0 when nothing answered
#   final   the URL the request actually ended on after following redirects
#   note    empty on success, otherwise the exception's short form
#
# Two things this deliberately does:
#   * -UseBasicParsing on every call. Without it PowerShell 5.1 hands the body
#     to the IE engine, which tries to prompt, and every single request dies
#     with "Windows PowerShell is in NonInteractive mode" rather than a status.
#   * HEAD first, then GET on failure. Plenty of hosts (hostelworld, viator)
#     refuse HEAD with a 403 or 405 while serving GET perfectly well, and
#     reporting those as dead links is the false positive that makes a link
#     report worth ignoring.
param(
    [Parameter(Mandatory = $true)][string]$InFile,
    [Parameter(Mandatory = $true)][string]$OutFile,
    [int]$Workers = 8,
    [int]$TimeoutSec = 20
)

$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11
[Net.ServicePointManager]::DefaultConnectionLimit = 64

$urls = @(Get-Content -LiteralPath $InFile -Encoding UTF8 | Where-Object { $_.Trim() -ne '' })
if ($urls.Count -eq 0) { Set-Content -LiteralPath $OutFile -Value '' -Encoding utf8; exit 0 }

$work = {
    param($u, $timeout)
    $ua = 'Mozilla/5.0 (compatible; getawayguide-linkcheck/1.0; +https://getawayguide.io)'
    foreach ($method in @('Head', 'Get')) {
        try {
            $r = Invoke-WebRequest -Uri $u -Method $method -MaximumRedirection 10 `
                 -TimeoutSec $timeout -UseBasicParsing -UserAgent $ua -ErrorAction Stop
            $final = $u
            if ($r.BaseResponse -and $r.BaseResponse.ResponseUri) {
                $final = $r.BaseResponse.ResponseUri.AbsoluteUri
            }
            return ,@($u, [int]$r.StatusCode, $final, '')
        }
        catch {
            $resp = $null
            if ($_.Exception.PSObject.Properties['Response']) { $resp = $_.Exception.Response }
            if ($resp) {
                $code = 0
                try { $code = [int]$resp.StatusCode } catch { $code = 0 }
                # HEAD is refused far more often than it is unsupported; give GET its turn
                if ($method -eq 'Head' -and @(403, 405, 429, 400, 501) -contains $code) { continue }
                $final = $u
                try { if ($resp.ResponseUri) { $final = $resp.ResponseUri.AbsoluteUri } } catch { }
                return ,@($u, $code, $final, '')
            }
            if ($method -eq 'Head') { continue }        # transport error: retry as GET once
            $msg = $_.Exception.Message -replace "`r|`n", ' '
            if ($msg.Length -gt 120) { $msg = $msg.Substring(0, 120) }
            return ,@($u, 0, $u, $msg)
        }
    }
    return ,@($u, 0, $u, 'no response')
}

$pool = [RunspaceFactory]::CreateRunspacePool(1, $Workers)
$pool.Open()
$jobs = @()
foreach ($u in $urls) {
    $ps = [PowerShell]::Create()
    $ps.RunspacePool = $pool
    [void]$ps.AddScript($work).AddArgument($u).AddArgument($TimeoutSec)
    $jobs += [pscustomobject]@{ PS = $ps; Handle = $ps.BeginInvoke(); Url = $u }
}

$rows = New-Object System.Collections.Generic.List[string]
foreach ($j in $jobs) {
    try {
        $res = $j.PS.EndInvoke($j.Handle)
        $r = @($res)[0]
        $rows.Add(("{0}`t{1}`t{2}`t{3}" -f $r[0], $r[1], $r[2], $r[3]))
    }
    catch {
        $rows.Add(("{0}`t0`t{1}`tinvoke failed" -f $j.Url, $j.Url))
    }
    finally { $j.PS.Dispose() }
}
$pool.Close(); $pool.Dispose()

# -Encoding utf8 explicitly: Set-Content defaults to the system ANSI codepage on
# this box, and a URL with a percent-free accented character comes back mangled.
Set-Content -LiteralPath $OutFile -Value $rows -Encoding utf8
Write-Output ("resolved {0} url(s)" -f $rows.Count)
