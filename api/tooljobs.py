"""Job runners for Image Edit / Upscale / Background removal and Video Edit / Upscale / Interpolate."""
import shutil
import time
import uuid
from pathlib import Path

import comfy
import db
import media
import resources
import workflows_tools as wt
from jobs import (_ensure_comfy, _progress_cb, _sampled, extract_frame, is_oom, next_output_path, presets, rel,
                  resolve_input)
from settings import (OUT_IMAGE_EDIT, OUT_IMAGE_UPSCALE, OUT_IMAGES, OUT_INTERPOLATE, OUT_NOBG, OUT_VIDEO_EDIT,
                      OUT_VIDEO_UPSCALE, OUT_VIDEOS, MODELS, TEMP)

WORK = TEMP / "work"
WORK.mkdir(parents=True, exist_ok=True)
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")


def _save_images(files, tag, folder=OUT_IMAGES):
    outs = []
    for f in files:
        dst = next_output_path(folder, tag, Path(f).suffix.lstrip("."))
        shutil.move(str(f), dst)
        outs.append(rel(dst))
    return outs


def _finish(job, outs, t0, sample, extra=None):
    secs = round(time.time() - t0, 1)
    db.update_generation(job.gen_id, outputs=outs, status="done", seconds=secs, peak_vram_mb=sample.get("peak"))
    job.result = {"outputs": outs, "seconds": secs, "peak_vram_mb": sample.get("peak"), **(extra or {})}


async def _prepare(job, key):
    await _ensure_comfy(job)
    return await resources.prepare("comfyui", key, notify=lambda m: setattr(job, "message", m))


# ─────────────────────────── image tools ───────────────────────────
async def run_image_edit(job):
    """Qwen-Image-2.1 instruction editing (also used for Qwen text-to-image when no image is given)."""
    p = dict(job.params)
    pr = presets().get("image_edit", {}).get(p.get("preset") or "balanced", {})
    for k, v in pr.items():
        p.setdefault(k, v)
    await _prepare(job, "image:qwen_image_21")
    uploaded, inputs = {}, {}
    if p.get("image"):
        src = resolve_input(p["image"])
        uploaded["image"], inputs["image"] = comfy.stage_input(src), rel(src)
    if p.get("mask"):
        src = resolve_input(p["mask"])
        uploaded["mask"], inputs["mask"] = comfy.stage_input(src), rel(src)
    for r in p.get("refs") or []:
        src = resolve_input(r)
        uploaded.setdefault("refs", []).append(comfy.stage_input(src))
        inputs.setdefault("refs", []).append(rel(src))
    graph, resolved = wt.build_qwen_image(p, uploaded)
    p.update(resolved)
    kind = "edit" if uploaded.get("image") else "generate"
    job.gen_id = db.add_generation(type="image", engine="comfyui", model="qwen-image-2.1", prompt=p.get("prompt"),
                                   negative=p.get("negative"), seed=resolved["seed"],
                                   params={k: v for k, v in p.items() if k not in ("prompt", "negative")},
                                   inputs=inputs, status="running")
    sample, t0 = {}, time.time()
    files = await _sampled(comfy.run(graph, _progress_cb(job)), sample)
    resources.mark_loaded("comfyui", "Qwen-Image-2.1")
    _finish(job, _save_images(files, "qwenedit" if kind == "edit" else "qwen", OUT_IMAGE_EDIT if kind == "edit" else OUT_IMAGES), t0, sample, {"seed": resolved["seed"]})
    await resources.after_job("comfyui")


async def run_image_upscale(job):
    p = dict(job.params)
    fast = p.get("quality") == "fast"
    await _prepare(job, "tool:esrgan" if fast else "tool:seedvr2")
    src = resolve_input(p["image"])
    graph, _ = wt.build_upscale_image(p, {"image": comfy.stage_input(src)})
    job.gen_id = db.add_generation(type="image", engine="comfyui", model="Real-ESRGAN x4" if fast else "SeedVR2 7B",
                                   prompt=f"Upscale ×{p.get('factor', 2)}", params=p, inputs={"image": rel(src)},
                                   status="running")
    sample, t0 = {}, time.time()
    try:
        files = await _sampled(comfy.run(graph, _progress_cb(job)), sample)
    except Exception as e:
        if not is_oom(e) or job.cancel_requested:
            raise
        job.message = "Not enough VRAM. Retrying with the lightweight upscaler…"
        await resources.unload_all()
        p["quality"] = "fast"
        graph, _ = wt.build_upscale_image(p, {"image": comfy.stage_input(src)})
        files = await _sampled(comfy.run(graph, _progress_cb(job)), sample)
    _finish(job, _save_images(files, "upscale", OUT_IMAGE_UPSCALE), t0, sample)
    await resources.after_job("comfyui")


async def run_remove_bg(job):
    p = dict(job.params)
    await _prepare(job, "tool:birefnet")
    src = resolve_input(p["image"])
    graph, _ = wt.build_remove_bg(p, {"image": comfy.stage_input(src)})
    job.gen_id = db.add_generation(type="image", engine="comfyui", model="BiRefNet", prompt="Remove background",
                                   params=p, inputs={"image": rel(src)}, status="running")
    sample, t0 = {}, time.time()
    files = await _sampled(comfy.run(graph, _progress_cb(job)), sample)
    _finish(job, _save_images(files, "nobg", OUT_NOBG), t0, sample)


# ─────────────────────────── video tools ───────────────────────────
def _video_input(p):
    src = resolve_input(p.get("video") or p.get("image"))
    if src.suffix.lower() not in VIDEO_EXT:
        raise ValueError("Please choose a video file.")
    return src


async def _run_segments(job, src, info, build, multiplier=1, out_w=None, out_h=None, fps_out=None, overlap=0,
                        tag="video", model_label="", budget_gb=3.0, folder=OUT_VIDEOS):
    """Split → process each segment in ComfyUI → join → re-mux original audio."""
    work = WORK / uuid.uuid4().hex[:8]
    work.mkdir(parents=True)
    ranges = media.plan_segments(info["frames"], out_w or info["width"], out_h or info["height"], multiplier,
                                 budget_gb=budget_gb, overlap=overlap)
    outs, sample = [], {}
    for i, (a, b) in enumerate(ranges):
        if job.cancel_requested:
            raise RuntimeError("Generation was cancelled.")
        seg = work / f"in_{i:03d}.mp4"
        media.cut_frames(src, a, b, seg)
        graph = build(comfy.stage_input(seg))
        base = i / len(ranges)

        def cb(frac, value, maximum, node, _base=base):
            _progress_cb(job, _base, 1 / len(ranges))(frac, value, maximum, node)
            job.message = f"Segment {i + 1}/{len(ranges)} · " + job.message.split(" · ")[-1]
        files = await _sampled(comfy.run(graph, cb, timeout=7200), sample)
        vids = [f for f in files if f.suffix.lower() in VIDEO_EXT]
        if not vids:
            raise RuntimeError("The backend returned no video.")
        outs.append(vids[0])
    job.message = "Joining segments and restoring audio…"
    dst = next_output_path(folder, tag, "mp4")
    media.concat_with_audio(outs, dst, audio_src=src if info["has_audio"] else None,
                            drop_first_frame_after_first=overlap > 0, fps=fps_out)
    for f in outs:
        Path(f).unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    return dst, sample


async def run_video_upscale(job):
    p = dict(job.params)
    src = _video_input(p)
    info = media.probe(src)
    fast = p.get("quality") == "fast"
    target = int(p["target_height"]) if p.get("target_height") else None
    if target:
        short = min(info["width"], info["height"])
        factor = target / short
    else:
        factor = float(p.get("factor", 2))
    out_w, out_h = int(info["width"] * factor) // 2 * 2, int(info["height"] * factor) // 2 * 2
    if out_w * out_h > 3840 * 2160:
        raise ValueError("Target resolution is above 4K. Please choose a smaller factor.")
    await _prepare(job, "tool:esrgan" if fast else "tool:seedvr2")
    job.gen_id = db.add_generation(type="video", engine="comfyui", model="Real-ESRGAN x4" if fast else "SeedVR2 7B",
                                   prompt=f"Video upscale → {out_w}×{out_h}", params={**p, "out": [out_w, out_h]},
                                   inputs={"video": rel(src)}, status="running")
    t0 = time.time()
    pp = {**p, "target_height": target and min(out_w, out_h), "factor": factor}
    dst, sample = await _run_segments(job, src, info, lambda name: wt.build_upscale_video_segment(pp, name),
                                      out_w=out_w, out_h=out_h, tag="upscale", folder=OUT_VIDEO_UPSCALE,
                                      budget_gb=1.5 if not fast else 3.0)
    _finish(job, [rel(dst)], t0, sample, {"width": out_w, "height": out_h})
    await resources.after_job("comfyui")


async def run_interpolate(job):
    p = dict(job.params)
    src = _video_input(p)
    info = media.probe(src)
    m = int(p.get("multiplier", 2))
    if p.get("target_fps") and float(info.get("fps") or 0) > 0:
        target_fps = float(p["target_fps"])
        m = min((2, 3, 4), key=lambda candidate: abs(float(info["fps"]) * candidate - target_fps))
    if m not in (2, 3, 4):
        raise ValueError("Multiplier must be 2, 3 or 4.")
    fps_out = round(info["fps"] * m, 3)
    await _prepare(job, "tool:rife")
    job.gen_id = db.add_generation(type="video", engine="comfyui", model="RIFE 4.25",
                                   prompt=f"Interpolate {info['fps']:g} → {fps_out:g} FPS", params=p,
                                   inputs={"video": rel(src)}, status="running")
    t0 = time.time()
    dst, sample = await _run_segments(job, src, info, lambda name: wt.build_interpolate_segment(p, name, fps_out),
                                      multiplier=m, fps_out=fps_out, overlap=1, tag="rife", folder=OUT_INTERPOLATE)
    _finish(job, [rel(dst)], t0, sample, {"fps": fps_out})
    await resources.after_job("comfyui")


async def run_video_edit(job):
    """VACE or Bernini-R: resample, edit a short segment, then re-mux its original audio."""
    p = dict(job.params)
    model_id = "vace" if p.get("model") == "vace" else "bernini"
    if model_id == "vace" and not (MODELS / "video" / "diffusion_models" / wt.VACE["unet"]).is_file():
        raise ValueError("Wan VACE weights are missing. Install the VACE model from the Models page/setup first.")
    src = _video_input(p)
    info = media.probe(src)
    pr = presets().get("video_edit", {}).get(p.get("preset") or "balanced", {})
    for k, v in pr.items():
        p.setdefault(k, v)
    if model_id == "vace":
        p.setdefault("steps", {"fast": 12, "balanced": 20, "quality": 30}.get(p.get("preset"), 20))
        p.setdefault("cfg", 6.0)
    fps = 16.0
    long_side = int(p.get("max_size", 832))
    w, h = media.fit_size(info["width"], info["height"], long_side)
    start = float(p.get("start", 0))
    avail = max(0.5, info["duration"] - start)
    frames = media.wan_frames(int(min(avail, float(p.get("duration", 5))) * fps), cap=int(p.get("max_frames", 81)))
    await _prepare(job, f"video:{model_id}")
    work = WORK / uuid.uuid4().hex[:8]
    work.mkdir(parents=True)
    clip = work / "source.mp4"
    media.prepare_clip(src, clip, fps, w, h, frames, start=start)
    uploaded, inputs = {}, {"video": rel(src)}
    if p.get("image"):
        ref = resolve_input(p["image"])
        uploaded["image"], inputs["image"] = comfy.stage_input(ref), rel(ref)
    graph, resolved = wt.build_video_edit(p, comfy.stage_input(clip), uploaded, w, h, frames, fps)
    model_label = "Wan 2.1 VACE 1.3B" if model_id == "vace" else "Bernini-R 14B"
    job.gen_id = db.add_generation(type="video", engine="comfyui", model=model_label, prompt=p.get("prompt"),
                                   seed=resolved["seed"], params={**p, **resolved}, inputs=inputs, status="running")
    sample, t0 = {}, time.time()
    try:
        files = await _sampled(comfy.run(graph, _progress_cb(job), timeout=7200), sample)
    except Exception as e:
        if not is_oom(e) or job.cancel_requested:
            raise
        job.message = "Not enough VRAM. Retrying at a smaller size…"
        await resources.unload_all()
        w, h = media.fit_size(info["width"], info["height"], 640)
        media.prepare_clip(src, clip, fps, w, h, frames, start=start)
        graph, resolved = wt.build_video_edit(p, comfy.stage_input(clip), uploaded, w, h, frames, fps)
        files = await _sampled(comfy.run(graph, _progress_cb(job), timeout=7200), sample)
    resources.mark_loaded("comfyui", model_label)
    vid = next(f for f in files if f.suffix.lower() in VIDEO_EXT)
    dst = next_output_path(OUT_VIDEO_EDIT, model_id, "mp4")
    job.message = "Restoring original audio…"
    media.concat_with_audio([vid], dst, audio_src=src if info["has_audio"] else None, audio_offset=start,
                            audio_duration=frames / fps)
    Path(vid).unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    _finish(job, [rel(dst)], t0, sample, resolved)
    await resources.after_job("comfyui")


RUNNERS = {"image_edit": run_image_edit, "image_upscale": run_image_upscale, "remove_bg": run_remove_bg,
           "video_upscale": run_video_upscale, "interpolate": run_interpolate, "video_edit": run_video_edit}
