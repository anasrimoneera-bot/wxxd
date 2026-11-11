@echo off
rem Auto-Favorite launcher.
rem Put this file in the same folder as Auto-Favorite.exe and start the app with it:
rem it downloads the newest Auto-Favorite.exe from GitHub only when there is a new one, then starts it.
rem This file is pure English (ASCII) on purpose, so it never shows garbled text.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText((Join-Path (Get-Location) '%~nx0')); iex $s.Substring($s.LastIndexOf('#'+'##PS'))"
if errorlevel 1 if not defined AUTO_FAVORITE_NO_START pause
exit /b

###PS  (everything below is PowerShell, run by the line above)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$repo  = 'anasrimoneera-bot/wxxd'
$tag   = 'auto-favorite'
$exe   = 'Auto-Favorite.exe'
$stamp = '.auto-favorite-version'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Get-Token {
    # 1) token.txt next to this file (a GitHub token with read access to the repository)
    if (Test-Path 'token.txt') {
        $t = (Get-Content 'token.txt' -Raw).Trim()
        if ($t) { return $t }
    }
    # 2) the GitHub login Git already saved on this PC (used by git pull)
    try {
        $env:GIT_TERMINAL_PROMPT = '0'
        $out = "protocol=https`nhost=github.com`n`n" | git credential fill 2>$null
        foreach ($line in $out) { if ($line -like 'password=*') { return $line.Substring(9) } }
    } catch {}
    return $null
}

$headers = @{ 'User-Agent' = 'Auto-Favorite-Updater'; 'Accept' = 'application/vnd.github+json' }
$token = Get-Token
if ($token) { $headers['Authorization'] = "Bearer $token" }

try {
    Write-Host 'Checking for a new version of Auto-Favorite...'
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/tags/$tag" -Headers $headers -TimeoutSec 30
    $asset = $rel.assets | Where-Object { $_.name -eq $exe } | Select-Object -First 1
    if (-not $asset) { throw 'Auto-Favorite.exe is not on the release page yet.' }
    $version = "$($asset.id) $($asset.updated_at)"
    $have = ''
    if (Test-Path $stamp) { $have = (Get-Content $stamp -Raw).Trim() }
    if ((Test-Path $exe) -and ($have -eq $version)) {
        Write-Host 'Already up to date.'
    } else {
        $mb = [math]::Round($asset.size / 1MB)
        Write-Host "Downloading the new version ($mb MB)..."
        $tmp = "$exe.download"
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curl) {
            $curlArgs = @('-fL', '--retry', '3', '-H', 'Accept: application/octet-stream', '-H', 'User-Agent: Auto-Favorite-Updater')
            if ($token) { $curlArgs += @('-H', "Authorization: Bearer $token") }
            & $curl.Source @curlArgs -o $tmp $asset.url
            if ($LASTEXITCODE -ne 0) { throw "download failed (curl exit code $LASTEXITCODE)" }
        } else {
            $h = $headers.Clone(); $h['Accept'] = 'application/octet-stream'
            Invoke-WebRequest -Uri $asset.url -Headers $h -OutFile $tmp -UseBasicParsing -TimeoutSec 1800
        }
        if ((Get-Item $tmp).Length -lt 1MB) { throw 'the downloaded file is too small, download failed' }
        try {
            Move-Item -Force $tmp $exe
        } catch {
            throw 'Auto-Favorite.exe is still open. Close it and run this file again.'
        }
        Set-Content -Path $stamp -Value $version
        Write-Host 'Updated to the newest version.'
    }
} catch {
    $msg = $_.Exception.Message
    if ($msg -match '404|Not Found|401|Unauthorized') {
        $msg = "$msg. GitHub did not allow the download: sign in once with git (git pull in your project folder), or put a GitHub token in token.txt next to this file."
    }
    Write-Host "Could not update: $msg"
    if (-not (Test-Path $exe)) {
        Write-Host 'There is no Auto-Favorite.exe in this folder yet, so nothing can be started.'
        exit 1
    }
    Write-Host 'Starting the version you already have.'
    Start-Sleep -Seconds 3
}

if ($env:AUTO_FAVORITE_NO_START) { exit 0 }
Start-Process -FilePath (Join-Path (Get-Location) $exe) -WorkingDirectory (Get-Location)
exit 0
