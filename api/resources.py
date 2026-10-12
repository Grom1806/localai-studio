"""Shared GPU resource manager.

Only one heavy "owner" family is kept in VRAM at a time:
    comfyui (image / video)  |  chatterbox  |  qwen  |  omnivoice

Modes (settings.vram_mode):
    auto        - before a job, unload other families if free VRAM < estimate + margin
                  (video always gets the GPU to itself)
    keep_loaded - only unload when VRAM is actually insufficient
    low_vram    - always unload everything else before a job and the job's own
                  model right after it finishes
"""
import json
import logging

import httpx
import psutil

import comfy
import gpu
import settings
from services import SERVICES
from settings import CONFIG

log = logging.getLogger("resources")

# What we believe is currently resident (updated by jobs and health polls).
state = {"loaded": {}}   # family -> model label


def estimate_gb(key: str) -> float:
    try:
        est = json.loads((CONFIG / "presets.json").read_text(encoding="utf-8-sig"))["vram_estimates_gb"]
        return float(est.get(key, 8.0))
    except Exception:
        return 8.0


async def unload_family(family: str):
    if family == "comfyui":
        await comfy.free(unload_models=True)
        state.pop("comfy_key", None)
    elif family == "llm":
        svc = SERVICES.get("llm")
        if svc and (svc.pid() or svc.status == "online"):
            await svc.stop()          # llama-server has no unload call: stopping it frees its VRAM
    else:
        svc = SERVICES.get(family)
        if svc and svc.status == "online":
            try:
                async with httpx.AsyncClient(timeout=30) as c:
                    await c.post(svc.url + "/unload")
            except Exception:
                pass
    if state["loaded"].pop(family, None):
        log.info("unloaded %s", family)


async def unload_all(except_family: str | None = None):
    for fam in ["comfyui", "chatterbox", "qwen", "omnivoice", "media", "llm"]:
        if fam != except_family:
            await unload_family(fam)


async def prepare(family: str, key: str, notify=None) -> dict:
    """Make room on the GPU for a job. Returns info about decisions taken."""
    mode = settings.get("vram_mode")
    need = estimate_gb(key)
    margin = float(settings.get("vram_margin_gb"))
    free_gb = gpu.vram_free_mb() / 1024
    others = [f for f in state["loaded"] if f != family]
    info = {"mode": mode, "need_gb": need, "free_gb_before": round(free_gb, 1), "unloaded": []}

    # SAM 2 video sessions keep temporal features in memory. Run them as an
    # exclusive vision job on the RTX 5080, even when the user normally keeps
    # other model families warm.
    if key.startswith("tool:sam2"):
        if notify:
            notify("Освобождаю видеопамять для SAM 2 и закрываю другие модели…")
        await unload_all(except_family="comfyui")
        await comfy.free(unload_models=True)
        state["loaded"].pop("comfyui", None)
        state.pop("comfy_key", None)
        info["unloaded"].append("all other model families")
        free_gb = gpu.vram_free_mb() / 1024

    # Switching between large ComfyUI models (image model <-> LTX <-> editor <-> Bernini <-> SeedVR2):
    # unload the previous one and clear the CUDA cache first, unless the user chose "keep loaded".
    if family == "comfyui":
        prev = state.get("comfy_key")
        heavy = lambda k: bool(k) and not k.startswith(("tool:rife", "tool:birefnet", "tool:esrgan"))
        # prev is unknown after an API restart: ComfyUI may still hold the last model, so check free VRAM instead
        unknown_but_full = prev is None and heavy(key) and free_gb < need + margin
        if (unknown_but_full or (prev and prev != key and heavy(prev) and heavy(key))) and mode != "keep_loaded":
            if notify:
                notify("Unloading the previous model to free VRAM…")
            await comfy.free(unload_models=True)
            state["loaded"].pop("comfyui", None)
            info["unloaded"].append(f"comfyui:{prev or 'unknown'}")
            free_gb = gpu.vram_free_mb() / 1024
        state["comfy_key"] = key

    must_unload = False
    if mode == "low_vram" or key.startswith("video:"):
        must_unload = True
    elif mode == "auto":
        must_unload = free_gb < need + margin
    elif mode == "keep_loaded":
        # The 12B multimodal editor model uses a long context and vision inputs;
        # keep the configured safety margin even when other models are normally retained.
        must_unload = free_gb < (need + margin if family == "llm" else need * 0.6)

    if must_unload:
        if notify:
            notify("Freeing GPU memory from other models…")
        # After an API restart our in-memory loaded-model registry may be empty
        # while ComfyUI still has weights resident. Ask it to free those too.
        if family != "comfyui" and "comfyui" not in others:
            await unload_family("comfyui")
            info["unloaded"].append("comfyui:untracked")
            free_gb = gpu.vram_free_mb() / 1024
        for f in others:
            await unload_family(f)
            info["unloaded"].append(f)
        free_gb = gpu.vram_free_mb() / 1024

    ram_avail = psutil.virtual_memory().available / 2**30
    if ram_avail < float(settings.get("ram_reserve_gb")):
        # system RAM is tight: drop everything cached (ComfyUI keeps offloaded weights in RAM)
        await unload_all(except_family=None if family != "comfyui" else None)
        info["ram_pressure"] = True

    info["free_gb_after"] = round(gpu.vram_free_mb() / 1024, 1)
    info["warning"] = None
    if family == "llm" and info["free_gb_after"] < need:
        info["warning"] = (f"Gemma‑4 vision analysis is estimated to need {need:.1f} GB VRAM; "
                            f"only {info['free_gb_after']:.1f} GB is free after model cleanup.")
        if notify:
            notify(info["warning"])
    if info["warning"] is None and info["free_gb_after"] < need * 0.5 and not key.startswith(("video:", "image:")):
        info["warning"] = "VRAM is low; generation may be slow."
    return info


def mark_loaded(family: str, label: str):
    state["loaded"][family] = label


async def after_job(family: str):
    if settings.get("vram_mode") == "low_vram":
        await unload_family(family)
        return
    # RAM guard: large models stay cached in system RAM after a job; release them if RAM is getting tight
    if psutil.virtual_memory().available / 2**30 < float(settings.get("ram_reserve_gb")):
        log.info("RAM below reserve after %s job: releasing cached models", family)
        await unload_family(family)
