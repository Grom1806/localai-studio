# Install LocalAI Studio with an AI coding agent

You can let an AI coding agent (Claude Code, Codex CLI, Cursor agent, …) install LocalAI Studio for you.
The prompt below makes it check your hardware first, follow the tested install path, avoid every
problem we already ran into, and stop to ask you before anything large or risky.

**How to use**

1. Clone the repository to exactly `C:\LocalAI`:
   ```bash
   git clone https://github.com/Grom1806/localai-studio.git C:\LocalAI
   ```
2. Open your AI agent **in the folder `C:\LocalAI`** (for Claude Code: open a terminal there and run `claude`).
3. Copy everything inside the box below and send it as your first message.
4. Answer its questions. A full install takes 1–4 hours, mostly downloads.

---

```text
You are installing "LocalAI Studio" on this Windows PC. The repository is already cloned to C:\LocalAI.
Reply to me in the language I write in. Explain each step in one or two plain sentences before you do it.

== HARD RULES (never break these, even if a command suggests otherwise) ==
1. Never change BIOS, GPU drivers, Windows security, antivirus, Windows Update, the page file / virtual
   memory, power plans or any other system setting. If something needs that, explain why and ask me.
2. Everything stays local: services bind to 127.0.0.1 only. Never open firewall ports, never expose
   anything to the network, never enable telemetry.
3. Download only from official sources: the model repos listed in config/models_manifest.json
   (Hugging Face orgs such as Comfy-Org, Lightricks, Qwen, ResembleAI, k2-fsa, google, fishaudio,
   Kijai, openai) and the official GitHub releases used by install.ps1. Nothing else.
4. Before ANY single download larger than 15 GB, show me: Model / Size / Purpose / Why, and wait for "yes".
5. Never delete or overwrite my files (outputs/, data/, voices/, anything outside C:\LocalAI).
   Temporary test files you create yourself must go to %TEMP% and be removed afterwards.
6. Tokens and passwords are typed by ME only (e.g. the Hugging Face login window). Never ask me to paste
   a token into the chat, never store one in a file you write, never commit secrets.
7. Do not modify ComfyUI or the voice engines' source code. Configure, don't patch.
8. If a step fails twice the same way, stop, show me the exact error, and propose a fix. Don't loop.

== PHASE 0 - HARDWARE GATE (do this first, before installing anything) ==
Run:  powershell -ExecutionPolicy Bypass -File C:\LocalAI\install.ps1 -CheckOnly
Read the report and tell me honestly what my PC can do, using this table:

| Level    | GPU (NVIDIA only)                    | What works                                             |
|----------|--------------------------------------|--------------------------------------------------------|
| not OK   | no NVIDIA / <8 GB VRAM / older than RTX 20 / driver <580 | nothing - stop and explain |
| LITE     | 8-11 GB VRAM                          | images + voice only (slow), NO video                  |
| STANDARD | 12-15 GB VRAM                         | images, voice, tools; video models very slow / OOM    |
| FULL     | 16 GB+ VRAM, RTX 50-series (Blackwell)| everything incl. LTX-2.5 video (NVFP4 needs RTX 50)   |

Also warn me plainly about: RAM below 32 GB (swapping with video models), free disk
(starter ~120 GB, video ~185 GB, full ~300 GB incl. environments), long download times, and that video
generation keeps the GPU at 100% for minutes (heat, noise, power draw; laptops should be plugged in).
If the check reports any [X] problem, STOP and explain how I can fix it. Do not try to work around it.
Recommend ONE model profile (starter / video / full) and say why.

== PHASE 1 - INSTALL ==
Use the tested installer, it is safe to re-run and skips finished steps:
   powershell -ExecutionPolicy Bypass -File C:\LocalAI\install.ps1 -Models <profile I approved>
It installs portable tools into C:\LocalAI\apps, Python environments from the exact lock files in
requirements\, builds the web UI, downloads models, and creates desktop shortcuts.
Commands are long-running: run them in the background and check logs\install.log instead of waiting
blindly. If Windows may go to sleep, tell me (the installer itself keeps the PC awake while downloading).
Gated models: before downloading, tell me to open these pages, sign in and click "Agree and access
repository", then the installer opens the Hugging Face login (I type the token myself):
   - https://huggingface.co/Lightricks/LTX-2.5            (video profiles)
   - https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf   (local LLM)
   - https://huggingface.co/pyannote/speaker-diarization-community-1 (optional: multi-speaker dubbing)

== KNOWN PITFALLS (already solved - recognise them, don't rediscover them) ==
- "python" opens the Microsoft Store: that's the Store stub. Never use it; uv installs Python into C:\LocalAI\apps\python.
- curl/Invoke-WebRequest SSL "revocation" errors: use curl.exe --ssl-no-revoke (installer does).
- Network drops during multi-GB downloads: re-run the same command; downloads resume, finished files are skipped.
- An interrupted `uv` install can leave a venv without python.exe: `uv venv <dir> --allow-existing`, then reinstall from the lock.
- Never `pip install` into an env without the lock file: each engine pins different torch/transformers
  versions on purpose (chatterbox, qwen-tts, omnivoice and fish-speech conflict with each other).
- torch must be the +cu130 build (cu128 for env-fish). A CPU torch means the wrong index was used.
- PowerShell 5.1 pitfalls: `*>` writes UTF-16 logs; Set-Content writes ANSI; JSON written by PowerShell
  may have a BOM (code reads with utf-8-sig); a hashtable with one pair can flatten into an array;
  `0x80000001` is a negative Int32 (use [Convert]::ToUInt32("80000001",16)).
- .bat/.cmd files need CRLF line endings (.gitattributes enforces it).
- Fish S2 Pro: needs tensorboard<2.21 with protobuf 5 (lock file has it). It uses ~16 GB VRAM and is
  ~25x slower than real time on a 16 GB card - optional, never the default engine.
- After an API restart ComfyUI may still hold the previous model in VRAM; the resource manager now frees it.
- InfiniteTalk: on a cold start the lip-sync patch can fail to attach (mouth barely moves); the app checks
  the first chunk and retries automatically.
- Windows sleep kills long downloads: the installer and benchmark scripts request "system required" while
  running (no power settings changed).
- RAM fills up (ComfyUI keeps offloaded weights in RAM): ComfyUI runs with --cache-ram 8 and a RAM guard;
  the "Free RAM" button in the UI unloads everything.
- After changing any code, restart LocalAI (Stop then Start) - the running server keeps the old code.

== PHASE 2 - VERIFY (don't claim success without this) ==
1. Start:  C:\LocalAI\Start-LocalAI.bat   then check http://127.0.0.1:8000/api/health returns 200
   and /api/services shows comfyui, media and the voice engines "online".
2. Open http://localhost:3000 and confirm the Dashboard shows my GPU.
3. Smoke tests that match the installed profile, one at a time:
   - Image: generate "a red fox in snow" with Z-Image Turbo (expect ~5-30 s).
   - Voice: speak one sentence in my language with the Auto engine.
   - Video (video/full only): 5 s LTX-2.5 "Preview" preset.
   Report time taken and peak VRAM for each (shown in the result / History page).
4. Stop LocalAI with Stop-LocalAI.bat when done, and confirm the GPU memory is released.

== PHASE 3 - REPORT ==
Finish with a short table: component / status / notes, the disk space used, what my PC can't do and
why, and any step you skipped. Never say something works unless you saw it work.
```

---

**Tip:** if the agent gets stuck, point it to [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md).
