# One HTTP request, driven from Python, using Windows' own TLS stack.
#
# Reason this exists: https from Python on this machine dies with
# CERTIFICATE_VERIFY_FAILED. Something local intercepts TLS, and its CA is
# (a) malformed -- Basic Constraints not marked critical, which OpenSSL 3
# rejects outright -- and (b) absent from certifi's bundle. Schannel, which is
# what Invoke-WebRequest uses, accepts it. So the fetch happens here and the
# parsing stays in Python.
#
# Called as:  powershell -File _ps_fetch.ps1 -Spec <spec.json> -Out <body.bin>
# The spec is {url, method, headers{}, body}. Response body lands in -Out.

param(
  [Parameter(Mandatory = $true)][string]$Spec,
  [Parameter(Mandatory = $true)][string]$Out
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$s = Get-Content -Raw -Encoding UTF8 $Spec | ConvertFrom-Json

# ConvertFrom-Json hands back a PSCustomObject and 5.1 has no -AsHashtable,
# so the header bag is rebuilt by hand.
$headers = @{}
$ua = $null
$ctype = $null
if ($s.headers) {
  foreach ($p in $s.headers.PSObject.Properties) {
    switch ($p.Name) {
      # .NET forbids these two in the generic header collection; they have
      # dedicated parameters and throw "header must be modified using the
      # appropriate property" if passed through -Headers.
      'User-Agent'   { $ua = $p.Value }
      'Content-Type' { $ctype = $p.Value }
      # Never ask for gzip here: IWR in 5.1 does not decompress, and the
      # caller would get a binary blob where it expected JSON.
      'Accept-Encoding' { }
      default        { $headers[$p.Name] = $p.Value }
    }
  }
}

$args = @{
  Uri             = $s.url
  Method          = $s.method
  Headers         = $headers
  OutFile         = $Out
  UseBasicParsing = $true
  TimeoutSec      = 45
}
if ($ua)    { $args['UserAgent']   = $ua }
if ($ctype) { $args['ContentType'] = $ctype }
if ($s.body) { $args['Body'] = [Text.Encoding]::UTF8.GetBytes($s.body) }

Invoke-WebRequest @args | Out-Null
