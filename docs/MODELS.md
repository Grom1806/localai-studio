# Models

LocalAI Studio ships **no model weights**. The installer downloads them from the official repositories
listed in [`config/models_manifest.json`](../config/models_manifest.json). Each model keeps its own
license; read it before using outputs commercially.

| Profile | Group | Models | Download | VRAM (peak) | License |
|---|---|---|---|---|---|
| starter | `image` | Z-Image Turbo 6B (bf16), FLUX.2 [klein] 4B, shared Qwen3-4B text encoder, Real-ESRGAN x4 | ~29 GB | 9–13 GB | Apache-2.0, BSD-3 |
| starter | `tts` | Chatterbox Multilingual, Qwen3-TTS 1.7B (Base + CustomVoice), OmniVoice | ~15 GB | 3–6 GB | MIT, Apache-2.0 |
| starter | `asr` | Whisper large-v3-turbo | ~1.6 GB | 2 GB | MIT |
| starter | `tools` | SeedVR2 7B (int8) upscaler, RIFE 4.25 interpolation, BiRefNet background removal | ~9 GB | 2–12 GB | Apache-2.0, MIT |
| starter | `llm` | Gemma-4 12B it QAT Q4_0 (GGUF, llama.cpp) | ~7 GB | ~8.5 GB | Gemma terms (gated) |
| video | `video` | LTX-2.5 22B distilled **NVFP4** + Gemma-4 int8 encoder, VAEs, x2 upscaler, duration head | ~37 GB | ~15 GB | LTX-2 Community (gated) |
| video | `infinitetalk` | Wan 2.1 I2V 14B fp8 + InfiniteTalk patch + wav2vec2 + lightx2v LoRA | ~23 GB | ~15 GB | Apache-2.0 |
| full | `qwen21` | Qwen-Image-2.1 7B int8 + Qwen3-VL 8B encoder | ~17 GB | ~14 GB | Apache-2.0 |
| full | `bernini` | Bernini-R 14B (Wan 2.2) fp8 video editor + lightx2v LoRA | ~39 GB | ~14 GB | Apache-2.0 |
| full | `hunyuan15` | HunyuanVideo 1.5 480p T2V/I2V + 720p super-resolution | ~39 GB | ~15 GB | Tencent Hunyuan Community |
| full | `vace` | Wan 2.1 VACE 1.3B | ~4 GB | ~11 GB | Apache-2.0 |
| full | `fish` | Fish Audio S2 Pro | ~10 GB | ~16 GB | Fish Audio Research (non-commercial) |

Optional components installable from the **Models** page: Stable Audio 3 Small-SFX (CPU, Stability AI
Community license), YuNet face detector (CPU, MIT), SAM 2.1 Hiera Tiny (Apache-2.0).

## Measured on RTX 5080 16 GB / 32 GB RAM

| Task | Time |
|---|---|
| Image 1024², Z-Image Turbo | ~5 s (warm) |
| Video 10 s 720p, LTX-2.5 *Balanced* | ~77 s |
| Video 5 s 720p, HunyuanVideo 1.5 *Fast* | ~3–9 min |
| Video edit 3 s, Bernini-R | ~130 s |
| Avatar 7 s, InfiniteTalk | ~205 s |
| 9:16 clip export 1080×1920, 30 s | ~17 s |

## Russian TTS benchmark (18 sentences, same reference voice)

| Engine | CER | Speaker similarity | UTMOS | RTF | VRAM |
|---|---|---|---|---|---|
| Qwen3-TTS | 0.5 % | 0.66 | **3.05** | 1.16 | 5.6 GB |
| OmniVoice | **0.3 %** | **0.73** | 2.54 | **0.32** | **2.8 GB** |
| Chatterbox | 0.7 % | 0.67 | 2.67 | 0.64 | 4.0 GB |
| Fish S2 Pro | **0.3 %** | 0.64 | 2.93 | 23.8 | 11 GB + RAM |

*Auto (Best Russian)* uses Qwen3-TTS → OmniVoice → Chatterbox. Scripts: `scripts/tests/ru_bench_*.py`.
