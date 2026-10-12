"""Job queue: one GPU job at a time, progress tracking, history records, human-readable errors."""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import traceback
import uuid
from pathlib import Path

import httpx

import comfy
import db
import gpu
import resources
import settings
import workflows
from services import SERVICES
from settings import CONFIG, FFMPEG, OUT_AUDIO, OUT_IMAGES, OUT_VIDEOS, UPLOADS

log = logging.getLogger("jobs")
CREATE_NO_WINDOW = 0x08000000


class Job:
    def __init__(self, kind, params):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind                 # image | video | voice
        self.params = params
        self.status = "queued"           # queued | running | done | error | cancelled
        self.progress = 0.0
        self.message = "Waiting in queue…"
        self.step = None
        self.result = None
        self.error = None
        self.created = time.time()
        self.started = None
        self.finished = None
        self.gen_id = None
        self.cancel_requested = False

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "status": self.status, "progress": round(self.progress, 3),
                "message": self.message, "step": self.step, "result": self.result, "error": self.error,
                "created": self.created, "started": self.started, "finished": self.finished,
                "elapsed": round((self.finished or time.time()) - (self.started or time.time()), 1),
                "generation_id": self.gen_id, "prompt": (self.params.get("prompt") or self.params.get("text") or "")[:200]}


JOBS: dict[str, Job] = {}
QUEUE: asyncio.Queue = None
EXTRA_RUNNERS: dict = {}     # registered by feature modules (dubbing, clips)
CURRENT: dict = {"job": None}


def presets():
    return json.loads((CONFIG / "presets.json").read_text(encoding="utf-8-sig"))


def submit(kind, params) -> Job:
    job = Job(kind, params)
    JOBS[job.id] = job
    QUEUE.put_nowait(job)
    # keep memory bounded
    if len(JOBS) > 300:
        for jid in sorted(JOBS, key=lambda k: JOBS[k].created)[:100]:
            if JOBS[jid].status in ("done", "error", "cancelled"):
                JOBS.pop(jid, None)
    return job


def queue_length():
    return sum(1 for j in JOBS.values() if j.status == "queued")


async def cancel(job_id):
    job = JOBS.get(job_id)
    if not job:
        return False
    job.cancel_requested = True
    if job.status == "queued":
        job.status, job.message = "cancelled", "Cancelled"
    elif job.status == "running" and job.kind != "voice":
        await comfy.interrupt()
    return True


async def worker():
    global QUEUE
    while True:
        job = await QUEUE.get()
        if job.status == "cancelled":
            continue
        CURRENT["job"] = job
        job.status, job.started, job.message = "running", time.time(), "Starting…"
        try:
            import tooljobs  # local import: tooljobs imports helpers from this module
            runners = {"image": run_image, "video": run_video, "voice": run_voice, **tooljobs.RUNNERS,
                       **EXTRA_RUNNERS}
            await runners[job.kind](job)
            job.status, job.progress, job.message = "done", 1.0, "Done"
        except Exception as e:
            human, detail = humanize(e)
            log.error("job %s (%s) failed: %s\n%s", job.id, job.kind, human, detail)
            job.status, job.error, job.message = ("cancelled" if job.cancel_requested else "error"), human, human
            if job.gen_id:
                db.update_generation(job.gen_id, status="error", error=human)
        finally:
            job.finished = time.time()
            CURRENT["job"] = None


# ─────────────────────────── helpers ───────────────────────────
def humanize(e: Exception) -> tuple[str, str]:
    detail = getattr(e, "detail", None) or "".join(traceback.format_exception(e))
    text = f"{e} {detail}".lower()
    if isinstance(e, (httpx.ConnectError, ConnectionRefusedError, OSError)) and "comfy" in text:
        return "ComfyUI backend is offline.", detail
    if "out of memory" in text or "outofmemory" in text or "allocation on device" in text:
        return "Not enough VRAM for these settings. Try a lower preset, resolution or duration.", detail
    if "missing" in text and "model" in text or "not in list" in text:
        return f"Model failed to load: {e}", detail
    if "cancel" in text or "interrupt" in text:
        return "Generation was cancelled.", detail
    if isinstance(e, httpx.ConnectError):
        return "A backend service is offline. Check the System page.", detail
    msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return f"Generation failed: {msg[:300]}", detail


def is_oom(e) -> bool:
    t = f"{e} {getattr(e, 'detail', '')}".lower()
    return "out of memory" in t or "outofmemory" in t or "allocation on device" in t


def next_output_path(folder: Path, model_tag: str, ext: str) -> Path:
    day = time.strftime("%Y-%m-%d")
    tag = re.sub(r"[^a-z0-9]+", "", model_tag.lower())[:16] or "gen"
    rx = re.compile(rf"^{day}_{tag}_(\d{{4}})")
    n = max([int(m.group(1)) for f in folder.glob(f"{day}_{tag}_*") if (m := rx.match(f.name))] + [0]) + 1
    return folder / f"{day}_{tag}_{n:04d}.{ext}"


def resolve_input(path_or_url: str | None) -> Path | None:
    """Accepts an absolute path inside LocalAI, or a /files/... URL from the UI."""
    if not path_or_url:
        return None
    p = path_or_url
    if p.startswith("/api/files/"):
        from urllib.parse import unquote
        p = unquote(p[len("/api/files/"):])
        p = str(settings.ROOT / p)
    path = Path(p).resolve()
    if not str(path).lower().startswith(str(settings.ROOT).lower()) or not path.exists():
        raise ValueError(f"Input file not found: {path_or_url}")
    return path


def rel(path: Path) -> str:
    return str(Path(path).resolve().relative_to(settings.ROOT)).replace("\\", "/")


def merge_preset(kind, p):
    pr = presets()
    name = p.get("preset") or settings.get(f"{kind}_default_preset")
    base = dict(pr.get(kind, {}).get(name, {}))
    if kind == "image":
        model = p.get("model") or base.get("model", "z_image_turbo")
        base.update(pr.get("image_models", {}).get(model, {}))
    base.update({k: v for k, v in p.items() if v not in (None, "")})
    base["preset"] = name
    return base


def _progress_cb(job, weight_start=0.0, weight=1.0):
    def cb(frac, value, maximum, node):
        if value is not None and maximum:
            job.step = f"{value}/{maximum}"
            job.message = {"KSampler": "Sampling", "SamplerCustomAdvanced": "Sampling"}.get(node, node) + f" {value}/{maximum}"
        elif node:
            job.message = {"UNETLoader": "Loading model…", "CLIPLoader": "Loading text encoder…",
                           "LTXAVTextEncoderLoader": "Loading text encoder…", "VAEDecode": "Decoding…",
                           "VAEDecodeTiled": "Decoding…", "CLIPTextEncode": "Encoding prompt…",
                           "SaveVideo": "Encoding MP4…", "SaveImage": "Saving…",
                           "LTXVLatentUpsampler": "Upscaling latent…"}.get(node, job.message)
        job.progress = min(0.99, weight_start + weight * frac)
    return cb


def _peak_vram(sample: dict):
    s = gpu.gpu_stats()
    sample["peak"] = max(sample.get("peak", 0), s.get("vram_used_mb", 0))


async def _sampled(coro, sample):
    async def poll():
        while True:
            _peak_vram(sample)
            await asyncio.sleep(0.5)
    t = asyncio.create_task(poll())
    try:
        return await coro
    finally:
        t.cancel()


async def _ensure_comfy(job):
    svc = SERVICES["comfyui"]
    if svc.status != "online" and not await svc.check():
        job.message = "Starting ComfyUI…"
        await svc.start()
        if not await svc.wait_online():
            raise RuntimeError("ComfyUI backend is offline.")


# ─────────────────────────── image ───────────────────────────
async def run_image(job: Job):
    p = merge_preset("image", job.params)
    await _ensure_comfy(job)
    model = p.get("model", "z_image_turbo")
    info = await resources.prepare("comfyui", f"image:{model}", notify=lambda m: setattr(job, "message", m))
    uploaded, inputs = {}, {}
    if p.get("image"):
        src = resolve_input(p["image"])
        uploaded["image"], inputs["image"] = comfy.stage_input(src), rel(src)
    if p.get("mask"):
        src = resolve_input(p["mask"])
        uploaded["mask"], inputs["mask"] = comfy.stage_input(src), rel(src)
    refs = []
    for r in p.get("refs") or []:
        src = resolve_input(r)
        refs.append(comfy.stage_input(src))
        inputs.setdefault("refs", []).append(rel(src))
    uploaded["refs"] = refs
    if uploaded.get("image") and p.get("mode", "t2i") == "t2i":
        p["mode"] = "edit" if model == "flux2_klein_4b" else "i2i"

    graph, resolved = workflows.build_image(p, uploaded)
    p.update(resolved)
    job.gen_id = db.add_generation(type="image", engine="comfyui", model=model, prompt=p.get("prompt"),
                                   negative=p.get("negative"), seed=resolved["seed"], params=_clean(p),
                                   inputs=inputs, status="running")
    sample = {}
    t0 = time.time()
    try:
        files = await _sampled(comfy.run(graph, _progress_cb(job)), sample)
    except Exception as e:
        if not is_oom(e) or job.cancel_requested:
            raise
        job.message = "Not enough VRAM. Retrying with LOW VRAM mode…"
        await resources.unload_all()
        p["tiled_vae"] = True
        graph, _ = workflows.build_image(p, uploaded)
        files = await _sampled(comfy.run(graph, _progress_cb(job)), sample)
    resources.mark_loaded("comfyui", workflows.IMAGE_MODELS[model]["label"])
    if p.get("_warmup"):  # "Load" button on the Models page: keep nothing
        for f in files:
            Path(f).unlink(missing_ok=True)
        db.delete_generation(job.gen_id)
        job.gen_id, job.result = None, {"outputs": [], "seconds": round(time.time() - t0, 1), "seed": 0}
        return
    outs = []
    for f in files:
        dst = next_output_path(OUT_IMAGES, model.replace("_turbo", "").replace("flux2_klein_4b", "flux2klein"),
                               f.suffix.lstrip("."))
        shutil.move(str(f), dst)
        outs.append(rel(dst))
    secs = round(time.time() - t0, 1)
    db.update_generation(job.gen_id, outputs=outs, status="done", seconds=secs, peak_vram_mb=sample.get("peak"))
    job.result = {"outputs": outs, "seconds": secs, "seed": resolved["seed"], "vram": info,
                  "peak_vram_mb": sample.get("peak")}
    await resources.after_job("comfyui")


# ─────────────────────────── video ───────────────────────────
async def run_video(job: Job):
    p = merge_preset("video", job.params)
    await _ensure_comfy(job)
    hy = p.get("model") == "hunyuan15"
    import workflows_tools
    build = workflows_tools.build_hunyuan if hy else workflows.build_video
    info = await resources.prepare("comfyui", "video:hunyuan15" if hy else "video:ltx25",
                                   notify=lambda m: setattr(job, "message", m))
    uploaded, inputs = {}, {}
    if p.get("image"):
        src = resolve_input(p["image"])
        if src.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv"):
            src = extract_frame(src, last=True)
        uploaded["image"], inputs["image"] = comfy.stage_input(src), rel(src)
        p["mode"] = "i2v"
    else:
        p["mode"] = "t2v"
    graph, resolved = build(p, uploaded)
    p.update(resolved)
    job.gen_id = db.add_generation(type="video", engine="comfyui", model="hunyuanvideo-1.5" if hy else "ltx-2.5-22b-distilled-nvfp4",
                                   prompt=p.get("prompt"), negative=p.get("negative"), seed=resolved["seed"],
                                   params=_clean(p), inputs=inputs, status="running")
    sample = {}
    t0 = time.time()
    try:
        files = await _sampled(comfy.run(graph, _progress_cb(job), timeout=7200), sample)
    except Exception as e:
        if not is_oom(e) or job.cancel_requested:
            raise
        job.message = "Not enough VRAM. Retrying with LOW VRAM mode (smaller decode tiles)…"
        await resources.unload_all()
        p.update(vae_tile=256, vae_temporal=32)
        graph, _ = build(p, uploaded)
        files = await _sampled(comfy.run(graph, _progress_cb(job), timeout=7200), sample)
    resources.mark_loaded("comfyui", "HunyuanVideo 1.5" if hy else "LTX-2.5 22B")
    outs = []
    for f in files:
        if f.suffix.lower() not in (".mp4", ".webm", ".mov"):
            continue
        dst = next_output_path(OUT_VIDEOS, "hunyuan" if hy else "ltx", "mp4")
        shutil.move(str(f), dst)
        outs.append(rel(dst))
    secs = round(time.time() - t0, 1)
    db.update_generation(job.gen_id, outputs=outs, status="done", seconds=secs, peak_vram_mb=sample.get("peak"))
    job.result = {"outputs": outs, "seconds": secs, "seed": resolved["seed"], "vram": info,
                  "peak_vram_mb": sample.get("peak"), **resolved}
    await resources.after_job("comfyui")


def extract_frame(video: Path, last=True) -> Path:
    out = UPLOADS / f"{video.stem}_{'last' if last else 'first'}.png"
    args = [str(FFMPEG), "-y", "-loglevel", "error"]
    args += (["-sseof", "-0.1"] if last else []) + ["-i", str(video), "-frames:v", "1", "-update", "1", str(out)]
    subprocess.run(args, check=True, capture_output=True, creationflags=CREATE_NO_WINDOW)
    return out


# ─────────────────────────── voice ───────────────────────────
async def run_voice(job: Job):
    p = dict(job.params)
    engine = p.get("engine") or "auto"
    if engine == "auto":
        from dubbing import pick_engine
        engine = pick_engine(p.get("language", "en"), "auto")
    preset = p.get("preset") or settings.get("voice_default_preset")
    params = dict(presets()["voice"].get(preset, {}).get(engine, {}))
    params.update(p.get("params") or {})
    svc = SERVICES[engine]
    if svc.status != "online" and not await svc.check():
        job.message = f"Starting {svc.label}…"
        await svc.start()
        if not await svc.wait_online():
            raise RuntimeError(f"{svc.label} backend is offline.")
    info = await resources.prepare(engine, f"voice:{engine}", notify=lambda m: setattr(job, "message", m))

    ref_audio, ref_text, inputs = None, p.get("ref_text"), {}
    if p.get("voice_id"):
        v = db.get_voice(int(p["voice_id"]))
        if not v:
            raise ValueError("Saved voice not found.")
        ref_audio = str(settings.ROOT / v["ref_path"])
        ref_text = ref_text or v.get("transcript")
        inputs["voice"] = {"id": v["id"], "name": v["name"], "ref": v["ref_path"]}
    elif p.get("ref_audio"):
        src = resolve_input(p["ref_audio"])
        ref_audio = str(ensure_wav(src))
        inputs["ref_audio"] = rel(src)

    out = next_output_path(OUT_AUDIO, engine, "wav")
    text = p["text"]
    if p.get("language", "en") == "ru" and p.get("normalize", True):
        import ru_text
        text = ru_text.normalize(text)
    body = {"text": text, "out_path": str(out), "language": p.get("language", "en"),
            "ref_audio": ref_audio, "ref_text": ref_text, "speaker": p.get("speaker"),
            "instruct": p.get("instruct"), "speed": float(p.get("speed", 1.0)),
            "seed": p.get("seed") if p.get("seed") not in (None, "") else None, "params": params}
    seed = body["seed"] if body["seed"] is not None and int(body["seed"]) >= 0 else int(time.time() * 1000) % 2**31
    body["seed"] = seed
    job.gen_id = db.add_generation(type="audio", engine=engine, model=svc.label, prompt=p["text"], seed=seed,
                                   params={k: v for k, v in body.items() if k not in ("text", "out_path")},
                                   inputs=inputs, status="running")
    job.message = "Loading voice model…" if not svc.health.get("loaded") else "Generating speech…"
    t0 = time.time()

    async def call():
        async with httpx.AsyncClient(timeout=1800) as c:
            r = await c.post(svc.url + "/generate", json=body)
        return r

    sample = {}
    job.progress = 0.1
    r = await _sampled(call(), sample)
    if r.status_code == 507:  # OOM inside worker: free everything and retry once
        job.message = "Not enough VRAM. Retrying with LOW VRAM mode…"
        await resources.unload_all(except_family=engine)
        r = await _sampled(call(), sample)
    data = r.json()
    if r.status_code != 200:
        raise RuntimeError(data.get("error", "Voice generation failed"))
    resources.mark_loaded(engine, svc.label)
    speed = float(p.get("speed", 1.0))
    if abs(speed - 1.0) > 0.01 and not data.get("speed_applied"):
        apply_tempo(out, speed)
    secs = round(time.time() - t0, 1)
    outs = [rel(out)]
    db.update_generation(job.gen_id, outputs=outs, status="done", seconds=secs, peak_vram_mb=sample.get("peak"))
    job.result = {"outputs": outs, "seconds": secs, "duration": data.get("duration"), "seed": seed, "vram": info,
                  "peak_vram_mb": sample.get("peak")}
    await resources.after_job(engine)


def ensure_wav(src: Path) -> Path:
    if src.suffix.lower() == ".wav":
        return src
    out = UPLOADS / (src.stem + "_ref.wav")
    if not out.exists():
        subprocess.run([str(FFMPEG), "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "24000", str(out)],
                       check=True, capture_output=True, creationflags=CREATE_NO_WINDOW)
    return out


def apply_tempo(path: Path, speed: float):
    speed = max(0.5, min(2.0, speed))
    tmp = path.with_suffix(".tmp.wav")
    subprocess.run([str(FFMPEG), "-y", "-loglevel", "error", "-i", str(path), "-filter:a", f"atempo={speed}", str(tmp)],
                   check=True, capture_output=True, creationflags=CREATE_NO_WINDOW)
    os.replace(tmp, path)


def to_mp3(path: Path) -> Path:
    out = path.with_suffix(".mp3")
    if not out.exists() or out.stat().st_mtime < path.stat().st_mtime:
        subprocess.run([str(FFMPEG), "-y", "-loglevel", "error", "-i", str(path), "-codec:a", "libmp3lame", "-q:a",
                        "2", str(out)], check=True, capture_output=True, creationflags=CREATE_NO_WINDOW)
    return out


def _clean(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in ("prompt", "negative") and not isinstance(v, bytes)}
