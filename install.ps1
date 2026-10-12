<#
  LocalAI Studio - one-step installer for Windows 10/11 + NVIDIA RTX.

  Run (from C:\LocalAI):   .\Install-LocalAI.bat            (or)   powershell -ExecutionPolicy Bypass -File install.ps1

  Options:
    -CheckOnly          only run the hardware/system check
    -Models <profile>   ask (default) | starter | video | full | none
    -Yes                do not ask for confirmations (sizes are still printed)
    -SkipEnvs           skip the Python environments (models/tools only)
    -NoShortcuts        do not create desktop shortcuts

  Safe to re-run: every step skips what is already installed. It never changes drivers, BIOS, Windows
  security, the page file or Windows Update, never opens firewall ports, and never deletes your files.
#>
param(
    [ValidateSet("ask", "starter", "video", "full", "none")] [string]$Models = "ask",
    [switch]$CheckOnly, [switch]$Yes, [switch]$SkipEnvs, [switch]$NoShortcuts
)
$ErrorActionPreference = "Stop"
$Root = "C:\LocalAI"
$V = @{                                   # pinned versions that are known to work together
    uv = "0.12.19"; comfy = "v0.37.0"; fish = "214da3cd841bda85da2496b96cd3c4d7edb1337e"; llama = "b11190"
}
$CU130 = "https://download.pytorch.org/whl/cu130"
$CU128 = "https://download.pytorch.org/whl/cu128"
$PROFILES = [ordered]@{
    starter = @{ groups = @("image", "tts", "asr", "tools", "llm"); gb = 62
                 what = "Images (Z-Image, FLUX.2 klein, upscalers, background removal), 3 voice engines, Whisper, local LLM" }
    video   = @{ groups = @("image", "tts", "asr", "tools", "llm", "video", "infinitetalk"); gb = 122
                 what = "starter + LTX-2.5 video with sound + InfiniteTalk lip sync / avatars" }
    full    = @{ groups = @("image", "tts", "asr", "tools", "llm", "video", "infinitetalk", "qwen21", "bernini", "hunyuan15", "vace", "fish"); gb = 231
                 what = "video + Qwen-Image editor, Bernini-R and VACE video editing, HunyuanVideo 1.5, Fish S2 Pro" }
}

function Say($m, $c = "Gray") { Write-Host "  $m" -ForegroundColor $c }
function Step($n, $t) { Write-Host ""; Write-Host "  [$n] $t" -ForegroundColor Cyan }
function Ask($q) {
    if ($Yes) { return $true }
    return ((Read-Host "  $q [y/N]") -match '^(y|yes|d|da)$')
}
function Run([string]$what, [scriptblock]$block, [int]$tries = 6) {
    # Runs a native command with retries (multi-GB downloads do drop) and stops the install on failure.
    for ($i = 1; $i -le $tries; $i++) {
        & $block 2>&1 | ForEach-Object { "    $_" } | Out-Host
        if ($LASTEXITCODE -eq 0) { return }
        $wait = [math]::Min(120, 15 * $i)
        Say "$what failed (attempt $i/$tries), retrying in $wait s..." Yellow
        Start-Sleep $wait
    }
    throw "$what failed after $tries attempts. See logs\install.log and docs\TROUBLESHOOTING.md"
}
function Get-File($url, $out) {
    # curl.exe with --ssl-no-revoke: Windows schannel often fails revocation checks behind some ISPs/AVs.
    Run "Download $(Split-Path $out -Leaf)" { curl.exe -L --fail --ssl-no-revoke --retry 5 --retry-delay 5 -o $out $url }
}
function Get-GhAsset($repo, $tag, $pattern) {
    $u = if ($tag) { "https://api.github.com/repos/$repo/releases/tags/$tag" } else { "https://api.github.com/repos/$repo/releases/latest" }
    $rel = Invoke-RestMethod $u -Headers @{ "User-Agent" = "LocalAI-Installer" }
    $a = $rel.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
    if (-not $a) { throw "No release asset matching '$pattern' in $repo $tag" }
    return $a.browser_download_url
}
function Expand-To($zip, $dest, [switch]$Flatten) {
    $tmp = Join-Path $env:TEMP ("localai_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    Expand-Archive $zip $tmp -Force
    $src = $tmp
    $items = Get-ChildItem $tmp
    if ($Flatten -and $items.Count -eq 1 -and $items[0].PSIsContainer) { $src = $items[0].FullName }
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item "$src\*" $dest -Recurse -Force
    Remove-Item $tmp -Recurse -Force
    Remove-Item $zip -Force
}

# ------------------------------------------------------------------------------------------------------
Write-Host ""
Write-Host "  =====================================" -ForegroundColor Magenta
Write-Host "   LocalAI Studio - installer" -ForegroundColor Magenta
Write-Host "  =====================================" -ForegroundColor Magenta

Step 1 "System check"
$sys = & "$PSScriptRoot\scripts\install\check_system.ps1"
if (-not $sys.ok) { Say "Installation stopped: fix the [X] problems above, then run the installer again." Red; exit 1 }
if ($CheckOnly) { exit 0 }
if ($sys.tier -ne "full" -and -not (Ask "Your PC is below the recommended level (see warnings above). Continue anyway?")) { exit 1 }

New-Item -ItemType Directory -Force "$Root\logs", "$Root\apps\bin", "$Root\envs", "$Root\models", "$Root\cache" | Out-Null
try { Start-Transcript "$Root\logs\install.log" -Append | Out-Null } catch {}
. "$Root\scripts\install\common.ps1"          # PATH (apps\bin first), uv cache/python dirs, hardlink mode
$env:UV_TOOL_DIR = "$Root\apps\uv-tools"; $env:UV_TOOL_BIN_DIR = "$Root\apps\bin"
$env:HF_HOME = "$Root\cache\hf"; $env:HF_HUB_DISABLE_TELEMETRY = "1"

# ------------------------------------------------------------------------------------------------------
Step 2 "Tools (portable, inside C:\LocalAI\apps - nothing is installed system-wide)"
if (-not (Test-Path "$Root\apps\bin\uv.exe")) {
    Say "uv $($V.uv)"
    Get-File "https://github.com/astral-sh/uv/releases/download/$($V.uv)/uv-x86_64-pc-windows-msvc.zip" "$env:TEMP\uv.zip"
    Expand-To "$env:TEMP\uv.zip" "$Root\apps\bin" -Flatten
} else { Say "uv: ok" }
if (-not (Test-Path "$Root\apps\ffmpeg\bin\ffmpeg.exe")) {
    Say "ffmpeg (BtbN GPL build)"
    Get-File "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" "$env:TEMP\ffmpeg.zip"
    Expand-To "$env:TEMP\ffmpeg.zip" "$Root\apps\ffmpeg" -Flatten
} else { Say "ffmpeg: ok" }
if (-not (Test-Path "$Root\apps\deno\deno.exe")) {
    Say "deno (JavaScript runtime that yt-dlp needs for YouTube)"
    Get-File "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" "$env:TEMP\deno.zip"
    Expand-To "$env:TEMP\deno.zip" "$Root\apps\deno"
} else { Say "deno: ok" }
if (-not (Test-Path "$Root\apps\yt-dlp\yt-dlp.exe")) {
    Say "yt-dlp"
    New-Item -ItemType Directory -Force "$Root\apps\yt-dlp" | Out-Null
    Get-File "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" "$Root\apps\yt-dlp\yt-dlp.exe"
} else { Say "yt-dlp: ok" }
if (-not (Test-Path "$Root\apps\llama.cpp\llama-server.exe")) {
    Say "llama.cpp $($V.llama) (CUDA 13) for the local LLM"
    Get-File (Get-GhAsset "ggml-org/llama.cpp" $V.llama 'llama-.*-bin-win-cuda-13.*-x64\.zip$') "$env:TEMP\llama.zip"
    Expand-To "$env:TEMP\llama.zip" "$Root\apps\llama.cpp" -Flatten
    Get-File (Get-GhAsset "ggml-org/llama.cpp" $V.llama '^cudart-llama-bin-win-cuda-13.*-x64\.zip$') "$env:TEMP\cudart.zip"
    Expand-To "$env:TEMP\cudart.zip" "$Root\apps\llama.cpp" -Flatten
} else { Say "llama.cpp: ok" }
if (-not (Test-Path "$Root\apps\bin\hf.exe")) {
    Say "Hugging Face CLI"
    Run "hf CLI" { uv tool install "huggingface_hub[cli]" }
} else { Say "hf CLI: ok" }
Run "Python 3.11 / 3.12" { uv python install 3.11 3.12 }

# ------------------------------------------------------------------------------------------------------
Step 3 "Source code of ComfyUI and Fish Speech (pinned versions)"
if (-not (Test-Path "$Root\apps\ComfyUI\main.py")) {
    Run "git clone ComfyUI" { git clone --depth 1 --branch $V.comfy https://github.com/comfyanonymous/ComfyUI.git "$Root\apps\ComfyUI" }
} else { Say "ComfyUI: ok" }
$wantFish = $Models -eq "full"
if ($wantFish -and -not (Test-Path "$Root\apps\fish-speech\pyproject.toml")) {
    Run "git clone fish-speech" { git clone https://github.com/fishaudio/fish-speech.git "$Root\apps\fish-speech" }
    Run "git checkout fish-speech" { git -C "$Root\apps\fish-speech" checkout $V.fish }
}

# ------------------------------------------------------------------------------------------------------
function New-Env($name, $pyver, [scriptblock]$install, $check) {
    $dir = "$Root\envs\$name"; $py = "$dir\Scripts\python.exe"
    if (Test-Path "$dir\.localai-ok") { Say "$name`: ok"; return }
    Say "$name (Python $pyver) - this can take 5-20 minutes" White
    Run "create $name" { uv venv $dir --python $pyver --allow-existing }
    & $install $py
    if ($check) {
        & $py -c $check 2>&1 | ForEach-Object { "    $_" } | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "$name was installed but its self-test failed (see above)." }
    }
    New-Item -ItemType File -Force "$dir\.localai-ok" | Out-Null
}
function Lock($py, $file, $index) {
    # Exact frozen package set; --no-deps because the lock already contains the full dependency closure.
    $a = @("pip", "install", "--python", $py, "--no-deps", "-r", "$Root\requirements\$file", "--index-strategy", "unsafe-best-match")
    if ($index) { $a += @("--extra-index-url", $index) }
    Run "install $file" { uv @a }
}
$cudaCheck = "import torch; assert torch.cuda.is_available(), 'CUDA not available'; x=torch.randn(512,512,device='cuda'); print('   torch', torch.__version__, 'GPU OK:', torch.cuda.get_device_name(0))"

if (-not $SkipEnvs) {
    Step 4 "Python environments (each engine has its own, so their versions never conflict)"
    New-Env "env-api" "3.11" { param($py) Lock $py "env-api.txt" } "import fastapi, httpx, psutil; print('   api OK')"
    New-Env "env-comfyui" "3.12" { param($py) Lock $py "env-comfyui.txt" $CU130 } $cudaCheck
    New-Env "env-media" "3.11" { param($py) Lock $py "env-media.txt" $CU130 } ($cudaCheck + "; import faster_whisper, demucs, mediapipe; print('   media OK')")
    New-Env "env-chatterbox" "3.11" { param($py) Lock $py "env-chatterbox.txt" $CU130 } $cudaCheck
    New-Env "env-omnivoice" "3.11" { param($py) Lock $py "env-omnivoice.txt" $CU130 } $cudaCheck
    New-Env "env-qwen" "3.11" {
        param($py)
        Run "torch for env-qwen" { uv pip install --python $py torch==2.14.0 torchaudio --index-url $CU130 }
        Run "qwen-tts" { uv pip install --python $py -r "$Root\requirements\env-qwen.in" }
    } $cudaCheck
    New-Env "env-face-blur" "3.11" { param($py) Lock $py "env-face-blur.txt" } "import cv2; print('   opencv', cv2.__version__)"
    if ($wantFish) {
        # Fish pins torch 2.8 (CUDA 12.8) and protobuf<6 -> isolated env; tensorboard<2.21 keeps protobuf 5 working.
        New-Env "env-fish" "3.11" { param($py) Lock $py "env-fish.txt" $CU128 } $cudaCheck
    }
}

# ------------------------------------------------------------------------------------------------------
Step 5 "Web interface"
if (-not (Test-Path "$Root\ui\dist\index.html")) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Say "Node.js is needed once to build the interface." Yellow
        if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install Node.js LTS with winget now?")) {
            Run "Node.js" { winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements }
            $env:Path += ";$env:ProgramFiles\nodejs"
        } else { throw "Install Node.js LTS from https://nodejs.org and run the installer again." }
    }
    Push-Location "$Root\ui"
    try { Run "npm ci" { npm ci --no-audit --no-fund }; Run "npm run build" { npm run build } } finally { Pop-Location }
} else { Say "interface: ok" }

# ------------------------------------------------------------------------------------------------------
Step 6 "AI models"
if ($Models -eq "ask") {
    $rec = if ($sys.tier -eq "full" -and $sys.blackwell) { "full or video" } else { "starter" }
    Say "Model profiles (download sizes; plus ~50 GB for Python environments and caches):" White
    foreach ($k in $PROFILES.Keys) { Say ("  {0,-8} ~{1,3} GB  {2}" -f $k, $PROFILES[$k].gb, $PROFILES[$k].what) }
    Say "  none              skip for now (run install.ps1 -Models <profile> later)"
    Say "Recommended for your PC: $rec   (free on C: $($sys.disk_free_gb) GB)" Green
    $Models = Read-Host "  Choose starter / video / full / none"
    if ($Models -notin @("starter", "video", "full", "none")) { $Models = "none" }
}
if ($Models -ne "none") {
    $prof = $PROFILES[$Models]
    if ($Models -ne "starter" -and -not $sys.blackwell) {
        Say "WARNING: '$Models' includes LTX-2.5 in NVFP4, which needs an RTX 50-series GPU. On your GPU video generation will fail or be extremely slow." Yellow
    }
    if ($prof.gb + 60 -gt $sys.disk_free_gb) { throw "Not enough disk space for '$Models' (~$($prof.gb + 60) GB needed with environments, $($sys.disk_free_gb) GB free)." }
    Say "About to download ~$($prof.gb) GB: $($prof.what)" White
    if (-not (Ask "Start the download? (can be stopped and resumed any time by re-running the installer)")) { $Models = "none" }
}
if ($Models -ne "none") {
    $gated = @()
    if ($prof.groups -contains "video") { $gated += "https://huggingface.co/Lightricks/LTX-2.5" }
    if ($prof.groups -contains "llm") { $gated += "https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf" }
    if ($gated -and -not (Test-Path "$Root\cache\hf\token")) {
        Say "Some models require accepting their license on Hugging Face (free account):" Yellow
        foreach ($g in $gated) { Say "  - open $g and click 'Agree and access repository'" Yellow }
        Say "  - optional, for multi-speaker dubbing: https://huggingface.co/pyannote/speaker-diarization-community-1" Yellow
        Read-Host "  Press Enter when done - a Hugging Face login window follows"
        & "$Root\scripts\hf_login.ps1"
    }
    # keep Windows awake during the long download (no power settings are changed)
    Add-Type -Namespace LocalAI -Name InstPower -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
    [LocalAI.InstPower]::SetThreadExecutionState([Convert]::ToUInt32("80000001", 16)) | Out-Null
    try {
        Run "model download" { & "$Root\envs\env-api\Scripts\python.exe" -u "$Root\scripts\download_models.py" @($prof.groups) } 3
    } finally {
        [LocalAI.InstPower]::SetThreadExecutionState([Convert]::ToUInt32("80000000", 16)) | Out-Null
    }
}

# ------------------------------------------------------------------------------------------------------
if (-not $NoShortcuts) {
    Step 7 "Desktop shortcuts"
    $ws = New-Object -ComObject WScript.Shell
    $desk = [Environment]::GetFolderPath("Desktop")
    $s = $ws.CreateShortcut("$desk\LocalAI Studio.lnk")
    $s.TargetPath = "$env:WINDIR\System32\wscript.exe"; $s.Arguments = "`"$Root\scripts\launch.vbs`""
    $s.IconLocation = "$Root\config\localai.ico"; $s.WorkingDirectory = $Root; $s.Save()
    $s = $ws.CreateShortcut("$desk\Stop LocalAI Studio.lnk")
    $s.TargetPath = "$Root\Stop-LocalAI.bat"; $s.IconLocation = "$Root\config\localai.ico"; $s.WorkingDirectory = $Root; $s.Save()
    Say "Created 'LocalAI Studio' and 'Stop LocalAI Studio' on the desktop."
}

Write-Host ""
Say "Done! Start LocalAI with the 'LocalAI Studio' desktop icon (or Start-LocalAI.bat)," Green
Say "then open http://localhost:3000 . Everything runs only on this PC (127.0.0.1)." Green
Say "Install log: $Root\logs\install.log" Gray
try { Stop-Transcript | Out-Null } catch {}
