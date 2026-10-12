<#
  LocalAI Studio - hardware and system check.
  Read-only: it changes nothing on the PC. Prints a report and returns an object the installer uses.

  Usage:  powershell -ExecutionPolicy Bypass -File scripts\install\check_system.ps1
#>
param([switch]$Quiet)

$ErrorActionPreference = "SilentlyContinue"
$root = "C:\LocalAI"
$r = [ordered]@{
    ok = $true; tier = "none"; blackwell = $false; problems = @(); warnings = @()
    gpu = $null; vram_gb = 0; compute = 0.0; driver = 0.0; ram_gb = 0; disk_free_gb = 0
}
function Fail($m) { $r.ok = $false; $r.problems += $m }
function Warn($m) { $r.warnings += $m }

# --- Windows -----------------------------------------------------------------------------------------
$os = Get-CimInstance Win32_OperatingSystem
$build = [int]$os.BuildNumber
if (-not [Environment]::Is64BitOperatingSystem) { Fail "64-bit Windows is required." }
if ($build -lt 19045) { Fail "Windows 10 22H2 (build 19045) or Windows 11 is required (found build $build)." }

# --- Install location (paths are fixed to C:\LocalAI) -------------------------------------------------
$here = (Resolve-Path "$PSScriptRoot\..\..").Path.TrimEnd('\')
if ($here -ne $root) { Fail "The repository must be at $root (it is at $here). Clone it with:  git clone <repo-url> C:\LocalAI" }

# --- NVIDIA GPU --------------------------------------------------------------------------------------
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if (-not $smi) {
    Fail "No NVIDIA GPU / driver found (nvidia-smi is missing). LocalAI needs an NVIDIA RTX GPU."
} else {
    $line = (& nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader,nounits | Select-Object -First 1)
    $p = $line -split ",\s*"
    $r.gpu = $p[0]
    $r.vram_gb = [math]::Round([double]$p[1] / 1024, 1)
    $r.driver = [double](($p[2] -split "\.")[0..1] -join ".")
    $r.compute = [double]$p[3]
    $r.blackwell = $r.compute -ge 12.0
    if ($r.compute -lt 7.5) { Fail "GPU $($r.gpu) (compute $($r.compute)) is too old. CUDA 13 needs an RTX 20-series or newer (compute 7.5+)." }
    if ($r.driver -lt 580) { Fail "NVIDIA driver $($p[2]) is too old for CUDA 13. Install driver 580 or newer from nvidia.com (the installer never changes drivers)." }
    if ($r.vram_gb -lt 8) { Fail "GPU has $($r.vram_gb) GB VRAM. At least 8 GB is required, 16 GB recommended." }
    elseif ($r.vram_gb -lt 12) { $r.tier = "lite"; Warn "$($r.vram_gb) GB VRAM: images and voice work (slower, with offloading). Video generation, video editing and lip sync are NOT supported." }
    elseif ($r.vram_gb -lt 15.5) { $r.tier = "standard"; Warn "$($r.vram_gb) GB VRAM: images, voice and tools work. The 14-22B video models will be very slow or run out of memory." }
    else { $r.tier = "full" }
    if (-not $r.blackwell -and $r.vram_gb -ge 12) {
        Warn "Your GPU is not an RTX 50-series (Blackwell). The LTX-2.5 video model is shipped in NVFP4, which only RTX 50 GPUs run natively - the 'video' and 'full' model profiles are not recommended."
    }
}

# --- RAM ---------------------------------------------------------------------------------------------
$r.ram_gb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
if ($r.ram_gb -lt 16) { Fail "$($r.ram_gb) GB RAM. At least 16 GB is required, 32 GB recommended." }
elseif ($r.ram_gb -lt 30) { Warn "$($r.ram_gb) GB RAM: big models are partly kept in system RAM. Expect swapping with video models; 32 GB+ is recommended." }
$pf = (Get-CimInstance Win32_PageFileUsage | Measure-Object AllocatedBaseSize -Sum).Sum
if ($r.ram_gb -lt 32 -and $pf -lt 16384) { Warn "The Windows page file is small ($pf MB). Loading 10-20 GB models can fail with 'out of memory'. Consider letting Windows manage the page file size (the installer does not change it)." }

# --- Disk --------------------------------------------------------------------------------------------
$r.disk_free_gb = [math]::Round((Get-PSDrive C).Free / 1GB)
if ($r.disk_free_gb -lt 120) { Fail "Only $($r.disk_free_gb) GB free on C:. Need 120 GB for the smallest setup (starter), about 300 GB for everything." }
elseif ($r.disk_free_gb -lt 300) { Warn "$($r.disk_free_gb) GB free on C:. Enough for 'starter' (~120 GB) or 'video' (~185 GB), not for 'full' (~300 GB)." }

# --- Tools / misc ------------------------------------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git is not installed (https://git-scm.com/download/win)." }
$lp = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled).LongPathsEnabled
if ($lp -ne 1) { Warn "Windows long paths are disabled. Usually fine because everything lives in C:\LocalAI, but some Python packages have very long paths." }
try { $null = Invoke-WebRequest "https://huggingface.co" -UseBasicParsing -TimeoutSec 10 -Method Head } catch { Fail "No connection to huggingface.co (needed to download models)." }

# --- Report ------------------------------------------------------------------------------------------
if (-not $Quiet) {
    Write-Host ""
    Write-Host "  LocalAI Studio - system check" -ForegroundColor Magenta
    Write-Host "  -----------------------------"
    Write-Host ("  GPU      {0}  ({1} GB VRAM, compute {2}, driver {3})" -f $r.gpu, $r.vram_gb, $r.compute, $r.driver)
    Write-Host ("  RAM      {0} GB" -f $r.ram_gb)
    Write-Host ("  Disk C:  {0} GB free" -f $r.disk_free_gb)
    Write-Host ("  Windows  build {0}" -f $build)
    $tierText = @{ none = "not supported"; lite = "LITE (images + voice)"; standard = "STANDARD (images, voice, tools; video limited)"; full = "FULL (everything)" }[$r.tier]
    Write-Host ("  Level    {0}" -f $tierText) -ForegroundColor $(if ($r.tier -eq "full") { "Green" } elseif ($r.tier -eq "none") { "Red" } else { "Yellow" })
    foreach ($w in $r.warnings) { Write-Host "  [!] $w" -ForegroundColor Yellow }
    foreach ($e in $r.problems) { Write-Host "  [X] $e" -ForegroundColor Red }
    if ($r.ok) { Write-Host "  System check passed." -ForegroundColor Green } else { Write-Host "  System check FAILED - fix the [X] items first." -ForegroundColor Red }
    Write-Host ""
}
[pscustomobject]$r
