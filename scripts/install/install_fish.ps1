# Fish Audio S2 Pro (fish-speech 2.0) in its own env: it pins torch 2.8.0 (cu128 supports RTX 50 / sm_120)
# and transformers<=4.57.3, which would break the other voice envs.
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\common.ps1"
$app = "C:\LocalAI\apps\fish-speech"
if (-not (Test-Path "$app\pyproject.toml")) { git clone -q https://github.com/fishaudio/fish-speech.git $app 2>&1 | Out-Host }
$py = "C:\LocalAI\envs\env-fish\Scripts\python.exe"
uv venv C:\LocalAI\envs\env-fish --python 3.11 --allow-existing 2>&1 | Out-Host
"== torch 2.8.0 cu128"
Invoke-Retry { uv pip install --python $py torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128 2>&1 } | Out-Host
"== fish-speech"
Invoke-Retry { uv pip install --python $py -e $app --override C:\LocalAI\scripts\install\fish_overrides.txt --index-url https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match 2>&1 } | Out-Host
Invoke-Retry { uv pip install --python $py fastapi "uvicorn[standard]" soundfile python-multipart 2>&1 } | Out-Host
"== verify"
& $py -c "import torch, fish_speech; print('torch', torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_capability(0)); x=torch.randn(1024,1024,device='cuda',dtype=torch.bfloat16); print('bf16 matmul ok', float((x@x).abs().mean()))" 2>&1
"== END"

