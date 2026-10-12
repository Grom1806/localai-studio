# Gracefully stops LocalAI Studio: asks the orchestrator to stop all backends, then cleans up leftovers.
$ErrorActionPreference = "SilentlyContinue"
$Root = "C:\LocalAI"
$api = 8000
if (Test-Path "$Root\data\ports.json") { $api = (Get-Content "$Root\data\ports.json" -Raw | ConvertFrom-Json).api }

Write-Host "  Stopping LocalAI Studio..."
try { Invoke-RestMethod -Method Post "http://127.0.0.1:$api/api/shutdown" -TimeoutSec 20 | Out-Null } catch {}
Start-Sleep 3

# Fallback: stop any remaining LocalAI processes (only Python processes running from C:\LocalAI\envs).
$pids = @()
foreach ($f in "$Root\data\pids.json", "$Root\data\api.pid") {
    if (Test-Path $f) {
        $raw = Get-Content $f -Raw
        if ($raw -match "^\s*\d+\s*$") { $pids += [int]$raw } else { ($raw | ConvertFrom-Json).PSObject.Properties | % { $pids += [int]$_.Value } }
    }
}
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.ExecutablePath -like "$Root\envs\*" -or $_.ExecutablePath -like "$Root\apps\python\*" } |
    ForEach-Object { $pids += [int]$_.ProcessId }
$pids = $pids | Sort-Object -Unique
$left = 0
foreach ($p in $pids) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$p"
    if ($proc -and ($proc.ExecutablePath -like "$Root\*")) { taskkill /PID $p /T /F | Out-Null; $left++ }
}
Remove-Item "$Root\data\pids.json", "$Root\data\api.pid" -ErrorAction SilentlyContinue
Write-Host "  LocalAI stopped. ($left leftover process(es) cleaned up)" -ForegroundColor Green
Start-Sleep 2
