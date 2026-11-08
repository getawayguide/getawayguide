# Google autocomplete harvester for keyword maps (roadmap step R6, 2026-11-07).
#
# Free source, no account: suggestqueries.google.com, the same list the search box shows.
# PowerShell does the fetching because Python's TLS is broken on this machine (see _ps_fetch.ps1).
# Generic browser User-Agent only. NEVER put an email address or any of Kevin's details in a request.
#
# Usage:
#   powershell -File tools/keyword_suggest.ps1 -Seeds .tmp/keywords/raw/kotor.seeds.txt -Out .tmp/keywords/raw/kotor.jsonl
# Seeds file: one seed per line ("kotor", "things to do in kotor", "kotor to perast").
# For each seed it asks for: the seed, question prefixes (how/what/is/can/best/...), and
# suffixes (" vs", " or", " to", " from", " in", " for", " with"). Each request -> one JSONL line
# {"seed","query","suggestions":[...]} in Google's order (order = rough relative popularity).
# Summarize with: python tools/keyword_summarize.py <jsonl> [...]

param(
  [Parameter(Mandatory = $true)][string]$Seeds,
  [Parameter(Mandatory = $true)][string]$Out,
  [int]$DelayMs = 150,
  [switch]$NoExpand
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
$prefixes = @('how', 'what', 'is', 'are', 'can you', 'do you need', 'best', 'where', 'when', 'why', 'which')
$suffixes = @(' ', ' vs', ' or', ' to', ' from', ' in', ' for', ' with', ' without', ' worth')

$utf8 = New-Object System.Text.UTF8Encoding($false)
$w = New-Object System.IO.StreamWriter($Out, $false, $utf8)
try {
  foreach ($seed in (Get-Content -Encoding UTF8 $Seeds)) {
    $seed = $seed.Trim()
    if (-not $seed -or $seed.StartsWith('#')) { continue }
    $queries = @($seed)
    if (-not $NoExpand) {
      foreach ($p in $prefixes) { $queries += "$p $seed" }
      foreach ($s in $suffixes) { $queries += "$seed$s" }
    }
    foreach ($q in $queries) {
      $url = 'https://suggestqueries.google.com/complete/search?client=firefox&hl=en&gl=us&q=' + [uri]::EscapeDataString($q)
      $sugs = @()
      for ($try = 0; $try -lt 3; $try++) {
        try {
          $r = Invoke-WebRequest -UseBasicParsing -UserAgent $ua -Uri $url -TimeoutSec 20
          # The endpoint answers in UTF-8; IWR 5.1 may decode it as Latin-1, so re-decode the raw bytes.
          $bytes = $r.RawContentStream.ToArray()
          $txt = [System.Text.Encoding]::UTF8.GetString($bytes)
          $j = $txt | ConvertFrom-Json
          $sugs = @($j[1])
          break
        } catch {
          Start-Sleep -Milliseconds (1500 * ($try + 1))
        }
      }
      $line = @{ seed = $seed; query = $q; suggestions = $sugs } | ConvertTo-Json -Compress
      $w.WriteLine($line)
      Start-Sleep -Milliseconds $DelayMs
    }
  }
} finally { $w.Close() }
