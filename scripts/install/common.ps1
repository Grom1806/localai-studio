$env:Path = "C:\LocalAI\apps\bin;" + [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
$env:UV_PYTHON_INSTALL_DIR = "C:\LocalAI\apps\python"
$env:UV_CACHE_DIR = "C:\LocalAI\cache\uv"
$env:UV_HTTP_TIMEOUT = "600"
$env:UV_HTTP_RETRIES = "10"
$env:UV_LINK_MODE = "hardlink"

# Run a command block until it succeeds (network drops happen during multi-GB downloads).
function Invoke-Retry([scriptblock]$Block, [int]$Tries = 8) {
    for ($i = 1; $i -le $Tries; $i++) {
        & $Block
        if ($LASTEXITCODE -eq 0) { return $true }
        "   (attempt $i failed, retrying in $([math]::Min(300, 30 * $i)) s)"
        Start-Sleep ([math]::Min(300, 30 * $i))
    }
    return $false
}
