# Troubleshooting

Every problem below actually happened while building LocalAI Studio. Logs are in `C:\LocalAI\logs\`
(`install.log`, `api.log`, `comfyui.console.log`, `<engine>.console.log`).

## Installation

| Symptom | Cause | Fix |
|---|---|---|
| `python` opens the Microsoft Store | Windows "App execution alias" stub | Ignore it. LocalAI uses its own Python in `apps\python` (installed by `uv`). |
| Download fails with an SSL / *revocation* error | Windows schannel cannot reach the certificate revocation server (ISP, antivirus) | The installer uses `curl.exe --ssl-no-revoke`. For manual downloads do the same. |
| Download stops half way | Network drop or Windows went to sleep | Re-run `install.ps1` – finished files are skipped, partial ones resume. The installer keeps Windows awake while downloading. |
| `envs\<name>\Scripts\python.exe` is missing | An interrupted `uv` run removed it | Re-run the installer (it recreates the env with `--allow-existing`). |
| `torch.cuda.is_available()` is `False` | CPU wheel installed (wrong index) or driver too old | Envs must use `+cu130` torch (`+cu128` for `env-fish`) and driver ≥ 580. Delete the env's `.localai-ok` file and re-run. |
| "No release asset matching …" | GitHub API rate limit (60 requests/hour without login) | Wait an hour, or download the llama.cpp CUDA 13 x64 zips manually into `apps\llama.cpp`. |
| `npm` not found | Node.js missing | Install Node.js LTS (the installer offers `winget`). Only needed once to build the UI. |
| Hugging Face 401 / 403 on LTX-2.5 or Gemma | Gated model: license not accepted or not logged in | Open the model page, click **Agree and access repository**, then run `HF-Login.bat`. |
| Repository not in `C:\LocalAI` | Paths are fixed to that folder | `git clone … C:\LocalAI` (moving the folder is not supported yet). |

## Running

| Symptom | Cause | Fix |
|---|---|---|
| Something still behaves "the old way" after an update | The server keeps the code it started with | Stop LocalAI and start it again. |
| VRAM full right after start | The previous model is still in ComfyUI after a restart | Fixed in the resource manager; or press **Free RAM** (trash icon, top right). |
| RAM at 90 %+ | ComfyUI keeps offloaded weights in system RAM | ComfyUI runs with `--cache-ram 8` plus a RAM guard; press **Free RAM** if needed. 32 GB RAM recommended. |
| Video is extremely slow / "out of memory" | GPU under 16 GB or not RTX 50 (LTX-2.5 is NVFP4) | Use the *Preview* preset, shorter clips, or HunyuanVideo 1.5 *Fast*. |
| Avatar mouth barely moves | InfiniteTalk patch not attached on a cold model load | The app now detects it on the first chunk and retries once automatically. |
| Fish S2 Pro takes minutes per sentence and fills VRAM | ~4B-parameter model, ~16 GB VRAM, ~25× slower than real time on 16 GB cards | Expected. Use Qwen3-TTS / OmniVoice; Fish is optional and never picked by *Auto*. |
| Fish fails: *incompatible Protobuf Gencode/Runtime* | tensorboard 2.21 needs protobuf 6, fish-speech pins protobuf < 6 | The lock file pins `tensorboard==2.20.0`. |
| Speaker detection skipped in dubbing | pyannote model is gated | Accept https://huggingface.co/pyannote/speaker-diarization-community-1 and log in with `HF-Login.bat`. |
| YouTube link fails | yt-dlp outdated or needs a JS runtime | Replace `apps\yt-dlp\yt-dlp.exe` with the latest release; `apps\deno\deno.exe` must exist. |
| Port already in use | Another program uses 3000 / 8000 / 8188 … | LocalAI picks the next free port automatically; ports are listed in **Settings**. |

## PowerShell gotchas (for contributors)

- Windows PowerShell 5.1 writes UTF-16 with `*>` and ANSI with `Set-Content` – use `-Encoding utf8` or Python for files other tools read.
- JSON written by PowerShell can start with a BOM – Python code reads config with `utf-8-sig`.
- `0x80000001` is a negative `Int32` in PowerShell – use `[Convert]::ToUInt32("80000001", 16)` for Win32 flags.
- Keep `.bat` / `.cmd` / `.ps1` files with CRLF line endings (`.gitattributes` does this).
