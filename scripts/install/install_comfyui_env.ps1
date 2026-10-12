$ErrorActionPreference = "Continue"
. "$PSScriptRoot\common.ps1"
$py = "C:\LocalAI\envs\env-comfyui\Scripts\python.exe"
if (-not (Test-Path $py)) { uv venv C:\LocalAI\envs\env-comfyui --python 3.12 --allow-existing 2>&1 }
"== torch 2.14.0 cu130"
Invoke-Retry { uv pip install --python $py torch==2.14.0 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130 2>&1 } | Out-Host
"== comfy requirements"
Invoke-Retry { uv pip install --python $py -r C:\LocalAI\apps\ComfyUI\requirements.txt 2>&1 } | Out-Host
"== verify"
& $py -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0)); x=torch.randn(4096,4096,device='cuda',dtype=torch.bfloat16); print('matmul ok', (x@x).float().abs().mean().item())" 2>&1
"== END"
