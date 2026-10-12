# Creates the three TTS environments. Each engine pins conflicting versions
# (chatterbox: transformers 5.2 / torch 2.6 pin; qwen-tts: transformers 4.57.3;
# omnivoice: transformers >= 5.3), so each gets its own venv. All use
# torch 2.14.0 + CUDA 13.0 wheels, which support the RTX 5080 (sm_120).
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\common.ps1"
$torchIdx = "https://download.pytorch.org/whl/cu130"
$common = @("fastapi", "uvicorn[standard]", "soundfile", "python-multipart")

function New-Env($name) {
    uv venv "C:\LocalAI\envs\$name" --python 3.11 --allow-existing 2>&1 | Out-Host
    $py = "C:\LocalAI\envs\$name\Scripts\python.exe"
    Invoke-Retry { uv pip install --python $py torch==2.14.0 torchaudio --index-url $torchIdx 2>&1 } | Out-Host
    return $py
}

"== env-chatterbox"
$py = New-Env "env-chatterbox"
# Official repo; --no-deps so its torch==2.6.0 pin (no Blackwell kernels) is not applied.
Invoke-Retry { uv pip install --python $py --no-deps "chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox.git" 2>&1 } | Out-Host
Invoke-Retry { uv pip install --python $py "numpy<2" "librosa==0.11.0" s3tokenizer "transformers==5.2.0" "diffusers==0.29.0" "resemble-perth>=1.0.0" "conformer==0.3.2" safetensors spacy-pkuseg "pykakasi==2.3.0" pyloudnorm omegaconf @common 2>&1 } | Out-Host

"== env-qwen"
$py = New-Env "env-qwen"
Invoke-Retry { uv pip install --python $py qwen-tts @common 2>&1 } | Out-Host

"== env-omnivoice"
$py = New-Env "env-omnivoice"
Invoke-Retry { uv pip install --python $py omnivoice @common 2>&1 } | Out-Host

"== verify"
foreach ($n in "env-chatterbox", "env-qwen", "env-omnivoice") {
    & "C:\LocalAI\envs\$n\Scripts\python.exe" -c "import torch,transformers;print('$n', torch.__version__, torch.cuda.is_available(), transformers.__version__)" 2>&1
}
"== END"
