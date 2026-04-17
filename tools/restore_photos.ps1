# restore_photos.ps1
# Restores all resized JPEG/PNG files to their previous OneDrive version.
# Uses Microsoft Graph API + device code OAuth (no premium required).
#
# SETUP (one-time, ~5 minutes, completely free):
# 1. Go to https://portal.azure.com and sign in with your Microsoft account
# 2. Search for "App registrations" in the top search bar → click it
# 3. Click "+ New registration"
#    - Name: anything (e.g. "Photo Restore")
#    - Account type: "Personal Microsoft accounts only"
#    - Click Register
# 4. On the app page, copy the "Application (client) ID" — paste it below
# 5. Click "Authentication" in the left menu
#    - Click "Add a platform" → "Mobile and desktop applications"
#    - Check the box for "https://login.microsoftonline.com/common/oauth2/nativeclient"
#    - Click Configure
#    - Under "Advanced settings", set "Allow public client flows" to YES
#    - Click Save
# 6. Click "API permissions" → "Add a permission" → "Microsoft Graph"
#    → "Delegated permissions" → search "Files.ReadWrite" → check it → Add
# 7. Paste your client ID on the line below, then run this script in PowerShell

param(
    [string]$ClientId = ""   # <-- paste your client ID here, or pass as argument
)

if (-not $ClientId) {
    $ClientId = Read-Host "Enter your Azure App client ID"
}

$TenantId = "consumers"
$Scope = "Files.ReadWrite offline_access"
$BaseFolder = "Documents/Travel Blog/Images"

# ---- Step 1: Device code auth ----
Write-Host "`nRequesting device code..." -ForegroundColor Cyan
$dcBody = "client_id=$ClientId&scope=$(([uri]::EscapeDataString($Scope)))"
$dcResp = Invoke-RestMethod -Uri "https://login.microsoftonline.com/$TenantId/oauth2/v2.0/devicecode" `
    -Method POST -Body $dcBody -ContentType "application/x-www-form-urlencoded"

Write-Host "`n$($dcResp.message)" -ForegroundColor Yellow
Write-Host "(Open the URL above in your browser, enter the code, sign in with your Microsoft account)`n"

# Poll for token
$tokenBody = "grant_type=urn:ietf:params:oauth:grant-type:device_code&client_id=$ClientId&device_code=$($dcResp.device_code)"
$token = $null
$deadline = (Get-Date).AddSeconds($dcResp.expires_in)
while ((Get-Date) -lt $deadline -and -not $token) {
    Start-Sleep -Seconds $dcResp.interval
    try {
        $token = Invoke-RestMethod -Uri "https://login.microsoftonline.com/$TenantId/oauth2/v2.0/token" `
            -Method POST -Body $tokenBody -ContentType "application/x-www-form-urlencoded" -ErrorAction Stop
    } catch {
        $err = $_.ErrorDetails.Message | ConvertFrom-Json -ErrorAction SilentlyContinue
        if ($err.error -eq "authorization_declined" -or $err.error -eq "expired_token") { break }
        # authorization_pending = keep waiting
    }
}
if (-not $token) { Write-Host "Authentication failed or timed out." -ForegroundColor Red; exit 1 }
$headers = @{ Authorization = "Bearer $($token.access_token)" }
Write-Host "Authenticated successfully." -ForegroundColor Green

# ---- Step 2: Find and restore all image files ----
function Get-DriveItems {
    param([string]$Path)
    $url = "https://graph.microsoft.com/v1.0/me/drive/root:/$([uri]::EscapeUriString($Path)):/children?`$top=200"
    $items = @()
    while ($url) {
        $resp = Invoke-RestMethod -Uri $url -Headers $headers
        $items += $resp.value
        $url = $resp.'@odata.nextLink'
    }
    return $items
}

function Restore-PreviousVersion {
    param([string]$ItemId, [string]$FileName)
    try {
        $verUrl = "https://graph.microsoft.com/v1.0/me/drive/items/$ItemId/versions"
        $versions = (Invoke-RestMethod -Uri $verUrl -Headers $headers).value
        if ($versions.Count -lt 2) {
            Write-Host "  SKIP $FileName (only 1 version, not yet synced to cloud)" -ForegroundColor DarkYellow
            return
        }
        # versions[0] = current (resized), versions[1] = previous (original)
        $prevId = $versions[1].id
        $restoreUrl = "https://graph.microsoft.com/v1.0/me/drive/items/$ItemId/versions/$prevId/restoreVersion"
        Invoke-RestMethod -Uri $restoreUrl -Method POST -Headers $headers | Out-Null
        Write-Host "  RESTORED $FileName" -ForegroundColor Green
    } catch {
        Write-Host "  ERROR $FileName`: $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Process-Folder {
    param([string]$Path)
    Write-Host "Scanning: $Path" -ForegroundColor Cyan
    $items = Get-DriveItems -Path $Path
    foreach ($item in $items) {
        if ($item.folder) {
            Process-Folder -Path "$Path/$($item.name)"
        } elseif ($item.name -match '\.(jpe?g|png)$') {
            Restore-PreviousVersion -ItemId $item.id -FileName $item.name
        }
    }
}

Write-Host "`nStarting restore of all images in $BaseFolder..." -ForegroundColor Cyan
Process-Folder -Path $BaseFolder
Write-Host "`nDone. OneDrive will now sync the restored originals back to your local folder." -ForegroundColor Green
Write-Host "Wait for OneDrive to finish syncing before refreshing the site." -ForegroundColor Yellow
