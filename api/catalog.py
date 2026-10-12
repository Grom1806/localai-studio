"""Installed-model catalog for the Models page."""
import re
from pathlib import Path

from settings import MODELS

# Logical models -> files (relative to models/). Shared files are listed in every model that uses them.
CATALOG = [
    {"id": "z_image_turbo", "name": "Z-Image Turbo 6B", "type": "image generation", "backend": "comfyui", "family": "comfyui",
     "precision": "bf16", "vram_gb": 13, "license": "Apache-2.0",
     "files": ["image/diffusion_models/z_image_turbo_bf16.safetensors", "shared/text_encoders/qwen_3_4b.safetensors",
               "image/vae/ae.safetensors"]},
    {"id": "flux2_klein_4b", "name": "FLUX.2 [klein] 4B", "type": "image editing (legacy)", "backend": "comfyui", "family": "comfyui",
     "precision": "bf16", "vram_gb": 9.5, "license": "Apache-2.0",
     "files": ["image/diffusion_models/flux-2-klein-4b.safetensors", "shared/text_encoders/qwen_3_4b.safetensors",
               "image/vae/flux2-vae.safetensors"]},
    {"id": "realesrgan_x4", "name": "Real-ESRGAN x4 (fast upscaler)", "type": "upscale", "backend": "comfyui",
     "family": "comfyui", "precision": "fp32", "vram_gb": 1, "license": "BSD-3",
     "files": ["image/upscale_models/RealESRGAN_x4plus.pth"]},
    {"id": "ltx25", "name": "LTX-2.5 22B distilled (video + audio)", "type": "video generation", "backend": "comfyui",
     "family": "comfyui", "precision": "nvfp4 + int8 text encoder", "vram_gb": 15, "license": "LTX-2 Community",
     "files": ["video/diffusion_models/ltx-2.5-22b-distilled-transformer-nvfp4.safetensors",
               "video/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
               "video/vae/ltx-2.5-video-vae-conv-bf16.safetensors",
               "video/vae/ltx-2.5-audio-vae-bf16.safetensors",
               "video/latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
               "video/model_patches/ltx-2.5-duration-head-bf16.safetensors"]},
    {"id": "qwen_image_21", "name": "Qwen-Image-2.1 7B (generate + edit)", "type": "image editing",
     "backend": "comfyui", "family": "comfyui", "precision": "int8", "vram_gb": 14, "license": "Apache-2.0",
     "files": ["image/diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
               "shared/text_encoders/qwen3vl_8b_int8_convrot.safetensors", "image/vae/qwen_image_2.1_vae_bf16.safetensors"]},
    {"id": "seedvr2", "name": "SeedVR2 7B (image + video upscaler)", "type": "upscale", "backend": "comfyui",
     "family": "comfyui", "precision": "int8", "vram_gb": 12, "license": "Apache-2.0",
     "files": ["tools/diffusion_models/seedvr2_7b_int8_convrot.safetensors", "tools/vae/seedvr2_ema_vae_fp16.safetensors"]},
    {"id": "rife", "name": "RIFE 4.25 heavy", "type": "frame interpolation", "backend": "comfyui", "family": "comfyui",
     "precision": "fp32", "vram_gb": 2, "license": "MIT", "files": ["tools/frame_interpolation/rife_v4.25_heavy.safetensors"]},
    {"id": "birefnet", "name": "BiRefNet", "type": "background removal", "backend": "comfyui", "family": "comfyui",
     "precision": "fp32", "vram_gb": 2, "license": "MIT", "files": ["tools/background_removal/birefnet.safetensors"]},
    {"id": "bernini", "name": "Bernini-R 14B (Wan 2.2) video editor", "type": "video editing", "backend": "comfyui",
     "family": "comfyui", "precision": "fp8", "vram_gb": 15.5, "license": "Apache-2.0",
     "files": ["video/diffusion_models/wan2.2_bernini_r_high_noise_fp8_scaled.safetensors",
               "video/diffusion_models/wan2.2_bernini_r_low_noise_fp8_scaled.safetensors",
               "video/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "video/vae/wan_2.1_vae.safetensors",
               "video/loras/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors"]},
    {"id": "vace", "name": "Wan 2.1 VACE 1.3B video editor", "type": "video editing", "backend": "comfyui",
     "family": "comfyui", "precision": "fp16", "vram_gb": 11, "license": "Apache-2.0",
     "files": ["video/diffusion_models/wan2.1_vace_1.3B_fp16.safetensors",
               "video/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "video/vae/wan_2.1_vae.safetensors"]},
    {"id": "chatterbox", "name": "Chatterbox Multilingual (23 languages)", "type": "voice", "backend": "chatterbox",
     "family": "chatterbox", "precision": "fp32", "vram_gb": 4, "license": "MIT", "dirs": ["tts/chatterbox"]},
    {"id": "qwen3_tts", "name": "Qwen3-TTS 1.7B (Base + CustomVoice)", "type": "voice", "backend": "qwen",
     "family": "qwen", "precision": "bf16", "vram_gb": 6, "license": "Apache-2.0",
     "dirs": ["tts/qwen3-tts-1.7b-base", "tts/qwen3-tts-1.7b-customvoice"]},
    {"id": "omnivoice", "name": "OmniVoice (600+ languages)", "type": "voice", "backend": "omnivoice",
     "family": "omnivoice", "precision": "fp16", "vram_gb": 4.5, "license": "Apache-2.0",
     "dirs": ["tts/omnivoice", "tts/whisper-large-v3-turbo"]},
    {"id": "stable_audio_3_sfx", "name": "Stable Audio 3 Small-SFX", "type": "sound effects generation",
     "backend": "LiteRT · CPU", "family": "stable_audio", "precision": "fp32 DiT + int8 codec", "vram_gb": 0,
     "license": "Stability AI Community", "installable": True,
     "storage_root": "apps/stable-audio-3/optimized/tflite",
     "files": ["models/tflite/sa3-sm-sfx/dit_fp32.tflite", "models/tflite/t5gemma/encoder_fp16.tflite",
               "models/tflite/same-s/enc_w8a8.tflite", "models/tflite/same-s/dec_w8a8.tflite"]},
    {"id": "yunet_face_blur", "name": "YuNet Face Detector (privacy blur)", "type": "face detection and blur", "backend": "OpenCV DNN · CPU",
     "family": "face_blur", "precision": "ONNX FP32", "vram_gb": 0, "license": "MIT", "installable": True,
     "files": ["vision/face_detection_yunet_2023mar.onnx"]},
    {"id": "sam2_video_mask", "name": "SAM 2.1 Hiera Tiny (видеомаски и tracking)", "type": "video segmentation and tracking",
     "backend": "Transformers · CUDA", "family": "comfyui", "precision": "bf16", "vram_gb": 5.5,
     "license": "Apache-2.0", "installable": True,
     "files": ["vision/sam2.1-hiera-tiny/config.json", "vision/sam2.1-hiera-tiny/model.safetensors",
               "vision/sam2.1-hiera-tiny/preprocessor_config.json", "vision/sam2.1-hiera-tiny/processor_config.json",
               "vision/sam2.1-hiera-tiny/video_preprocessor_config.json"]},
]

USER_DIRS = {"image/loras": "LoRA (image)", "video/loras": "LoRA (video)", "image/checkpoints": "Checkpoint",
             "image/controlnet": "ControlNet"}


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return 0


def _precision(name: str) -> str:
    for tag in ("nvfp4", "fp4", "fp8", "int8", "bf16", "fp16", "gguf", "q4", "q5", "q8"):
        if re.search(tag, name, re.I):
            return tag
    return "?"


def listing(loaded: dict) -> list[dict]:
    out = []
    for m in CATALOG:
        base = MODELS.parent / m["storage_root"] if m.get("storage_root") else MODELS
        paths = [base / f for f in m.get("files", [])] + [base / d for d in m.get("dirs", [])]
        present = [p for p in paths if p.exists()]
        size = sum(_size(p) for p in present)
        out.append({**{k: v for k, v in m.items() if k not in ("files", "dirs")},
                    "installed": len(present) == len(paths) and size > 0,
                    "partial": 0 < len(present) < len(paths),
                    "size_gb": round(size / 1e9, 2),
                    "paths": [str(p) for p in paths],
                    "folder": str(paths[0].parent if m.get("files") else paths[0]),
                    "loaded": _is_loaded(m, loaded), "deletable": not bool(m.get("storage_root"))})
    for rel_dir, label in USER_DIRS.items():
        d = MODELS / rel_dir
        for f in sorted(d.glob("*")) if d.exists() else []:
            if f.is_file() and f.suffix in (".safetensors", ".pt", ".pth", ".ckpt", ".gguf"):
                out.append({"id": f"user:{rel_dir}/{f.name}", "name": f.name, "type": label, "backend": "comfyui",
                            "family": "comfyui", "precision": _precision(f.name), "vram_gb": None,
                            "installed": True, "size_gb": round(f.stat().st_size / 1e9, 2), "paths": [str(f)],
                            "folder": str(d), "loaded": False, "deletable": True, "user": True})
    return out


def _is_loaded(m, loaded):
    label = loaded.get(m["family"])
    if not label:
        return False
    if m["family"] != "comfyui":
        return True
    return m["name"].split(" ")[0].lower() in label.lower() or m["id"].split("_")[0] in label.lower()


def files_to_delete(model_id: str) -> list[Path]:
    """Files belonging only to this model (shared files that another installed model uses are kept)."""
    if model_id.startswith("user:"):
        return [MODELS / model_id[5:]]
    m = next((x for x in CATALOG if x["id"] == model_id), None)
    if not m:
        return []
    base = MODELS.parent / m["storage_root"] if m.get("storage_root") else MODELS
    others = set()
    for x in CATALOG:
        if x["id"] != model_id:
            others.update(x.get("files", []))
    return [base / f for f in m.get("files", []) if f not in others] + [base / d for d in m.get("dirs", [])]
