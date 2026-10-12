# LocalAI Studio launcher: starts the orchestrator in the background (it starts ComfyUI and the
# TTS workers itself), waits for health checks, then opens the browser. No windows stay open.
param([switch]$Silent)
$ErrorActionPreference = "SilentlyContinue"
$Root = "C:\LocalAI"
$py = "$Root\envs\env-api\Scripts\python.exe"

function Get-Ports {
    $p = @{ ui = 3000; api = 8000 }
    if (Test-Path "$Root\data\ports.json") {
        $j = Get-Content "$Root\data\ports.json" -Raw | ConvertFrom-Json
        $p.ui = $j.ui; $p.api = $j.api
    }
    return $p
}
function Test-Api($port) {
    try { (Invoke-WebRequest "http://127.0.0.1:$port/api/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false }
}

Write-Host ""
Write-Host "  LocalAI Studio" -ForegroundColor Magenta
Write-Host "  ---------------"

$ports = Get-Ports
if (-not (Test-Api $ports.api)) {
    Write-Host "  Starting services..." -NoNewline
    # rotate the orchestrator console log
    $cl = "$Root\logs\api.console.log"
    if ((Test-Path $cl) -and (Get-Item $cl).Length -gt 5MB) { Move-Item $cl "$cl.1" -Force }
    Start-Process -FilePath $py -ArgumentList "-u", "$Root\api\main.py" -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput $cl -RedirectStandardError "$Root\logs\api.console.err.log"
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 700
        $ports = Get-Ports
        if (Test-Api $ports.api) { $ok = $true; break }
        Write-Host "." -NoNewline
    }
    Write-Host ""
    if (-not $ok) {
        Write-Host "  LocalAI failed to start. See $Root\logs\api.log" -ForegroundColor Red
        Start-Sleep 8
        exit 1
    }
} else {
    Write-Host "  Already running."
}

# Wait (max ~4 min) for ComfyUI; TTS workers come up in parallel and are shown in the UI.
Write-Host "  Waiting for backends" -NoNewline
for ($i = 0; $i -lt 120; $i++) {
    try {
        $s = Invoke-RestMethod "http://127.0.0.1:$($ports.api)/api/status" -TimeoutSec 3
        $c = $s.services.comfyui.status
        if ($c -eq "online" -or $c -eq "error") { break }
    } catch {}
    Write-Host "." -NoNewline
    Start-Sleep 2
}
Write-Host ""
foreach ($n in "comfyui", "chatterbox", "qwen", "omnivoice") {
    $st = $s.services.$n.status
    $col = if ($st -eq "online") { "Green" } elseif ($st -eq "starting") { "Yellow" } else { "Red" }
    Write-Host ("  {0,-11} {1}" -f $n, $st.ToUpper()) -ForegroundColor $col
}
$url = "http://localhost:$($ports.ui)"
$settings = Get-Content "$Root\config\settings.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
if (-not $Silent -and ($null -eq $settings -or $settings.open_browser -ne $false)) { Start-Process $url }
Write-Host "  Open: $url" -ForegroundColor Cyan
if (-not $Silent) { Start-Sleep 3 }
