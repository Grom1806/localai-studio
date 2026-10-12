# env-media: transcription (faster-whisper / CTranslate2), vocal separation (Demucs), speaker diarization
# (pyannote), face tracking (MediaPipe). Separate from the TTS/ComfyUI envs so those stay untouched.
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\common.ps1"
$py = "C:\LocalAI\envs\env-media\Scripts\python.exe"
uv venv C:\LocalAI\envs\env-media --python 3.11 --allow-existing 2>&1 | Out-Host
"== torch (cached cu130 build)"
Invoke-Retry { uv pip install --python $py torch==2.14.0 torchaudio --index-url https://download.pytorch.org/whl/cu130 2>&1 } | Out-Host
"== media packages"
Invoke-Retry { uv pip install --python $py faster-whisper "ctranslate2>=4.8" demucs "pyannote.audio>=4.0" mediapipe opencv-python-headless soundfile librosa fastapi "uvicorn[standard]" python-multipart httpx "numpy<2.3" 2>&1 } | Out-Host
"== CUDA 12 runtime libs for CTranslate2 (Windows wheels link cuBLAS 12 / cuDNN 9)"
Invoke-Retry { uv pip install --python $py nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10" 2>&1 } | Out-Host
"== verify"
& $py -c "import torch, ctranslate2, faster_whisper, demucs, mediapipe, cv2; print('torch', torch.__version__, torch.cuda.is_available()); print('ct2', ctranslate2.__version__, 'cuda devices', ctranslate2.get_cuda_device_count()); print('mediapipe', mediapipe.__version__, 'cv2', cv2.__version__)" 2>&1
& $py -c "import pyannote.audio; print('pyannote', pyannote.audio.__version__)" 2>&1
"== END"
