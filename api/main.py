"""LocalAI Studio orchestrator.

Serves the web UI and the JSON API on 127.0.0.1:3000 (UI) and 127.0.0.1:8000 (API),
starts/monitors backend services, and runs generation jobs one at a time.
"""
import asyncio
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import catalog  # noqa: E402
import cleanup  # noqa: E402
import comfy  # noqa: E402
import db  # noqa: E402
import gpu  # noqa: E402
import jobs  # noqa: E402
import resources  # noqa: E402
import services  # noqa: E402
import settings  # noqa: E402
from settings import DATA, LOGS, MODELS, ROOT, SERVABLE_ROOTS, UI_DIST, UPLOADS, VOICES  # noqa: E402

log = logging.getLogger("api")
CREATE_NO_WINDOW = 0x08000000


def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    h = logging.handlers.RotatingFileHandler(LOGS / "api.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    s = logging.StreamHandler(sys.stdout)
    s.setFormatter(fmt)
    root.addHandler(s)
    for noisy in ("httpx", "uvicorn.access", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


app = FastAPI(title="LocalAI Studio", docs_url="/api/docs", openapi_url="/api/openapi.json")
EDITOR_PROJECTS_DIR = DATA / "video_projects"
EDITOR_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def _editor_project_path(project_id: str) -> Path:
    if not project_id or len(project_id) > 80 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in project_id):
        raise HTTPException(400, "Invalid project id.")
    return EDITOR_PROJECTS_DIR / f"{project_id}.json"


def _editor_project_summary(path: Path, value: dict) -> dict:
    document = value.get("document", {})
    state = document.get("state", {}) if isinstance(document, dict) else {}
    return {"id": path.stem, "name": str(value.get("name") or "Новый проект"),
            "updated_at": value.get("updated_at"), "source_count": len(state.get("sources", [])) if isinstance(state, dict) else 0}


def file_url(rel: str) -> str:
    return "/api/files/" + quote(rel.replace("\\", "/"))


# ───────────────────────────── status ─────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "time": time.time()}


@app.get("/api/status")
def status():
    cur = jobs.CURRENT["job"]
    return {
        "gpu": gpu.gpu_stats(), **gpu.ram_stats(), **gpu.disk_stats(), **gpu.cpu_stats(),
        "services": {n: s.to_dict() for n, s in services.SERVICES.items()},
        "current_job": cur.to_dict() if cur else None,
        "queue_length": jobs.queue_length(),
        "loaded": resources.state["loaded"],
        "vram_mode": settings.get("vram_mode"),
        "video_installed": all((MODELS / f).exists() for f in next(m for m in catalog.CATALOG if m["id"] == "ltx25")["files"]),
        "stats": db.stats(),
    }


@app.get("/api/services")
def list_services():
    return [s.to_dict() for s in services.SERVICES.values()]


@app.post("/api/services/{name}/{action}")
async def service_action(name: str, action: str):
    svc = services.SERVICES.get(name)
    if not svc:
        raise HTTPException(404, "unknown service")
    cur = jobs.CURRENT["job"]
    if action in ("restart", "stop") and cur and _job_family(cur) == name:
        raise HTTPException(409, "This backend is busy with a generation. Try again when it finishes.")
    if action == "restart":
        resources.state["loaded"].pop(name, None)
        ok = await svc.restart()
        return {"ok": ok, **svc.to_dict()}
    if action == "start":
        await svc.start()
        return svc.to_dict()
    if action == "stop":
        resources.state["loaded"].pop(name, None)
        await svc.stop()
        return svc.to_dict()
    raise HTTPException(400, "unknown action")


def _job_family(job):
    if job.kind == "voice":
        return job.params.get("engine") or settings.get("voice_default_engine")
    return "comfyui"


# ───────────────────────────── presets / settings ─────────────────────────────
@app.get("/api/presets")
def get_presets():
    return jobs.presets()


@app.get("/api/settings")
def get_settings():
    return settings.load()


@app.put("/api/settings")
def put_settings(patch: dict):
    if "vram_mode" in patch and patch["vram_mode"] not in ("auto", "keep_loaded", "low_vram"):
        raise HTTPException(400, "invalid vram_mode")
    return settings.save(patch)


@app.get("/api/options")
async def options():
    """Model lists / languages / speakers for the UI forms."""
    loras = sorted(f.name for f in (MODELS / "image" / "loras").glob("*.safetensors"))
    voices_info = {}
    for name in ("chatterbox", "qwen", "omnivoice", "fish"):
        svc = services.SERVICES[name]
        if svc.status == "online":
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    voices_info[name] = (await c.get(svc.url + "/info")).json()
            except Exception:
                pass
    import workflows
    return {"image_models": {k: {"label": v["label"], "supports": v["supports"]} for k, v in workflows.IMAGE_MODELS.items()},
            "loras": loras, "tts": voices_info,
            "samplers": ["res_multistep", "euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde", "uni_pc"],
            "schedulers": ["simple", "normal", "beta", "karras", "sgm_uniform"]}


# ───────────────────────────── generation ─────────────────────────────
class ImageReq(BaseModel):
    prompt: str
    negative: str | None = None
    model: str | None = None
    preset: str | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    seed: int | None = -1
    cfg: float | None = None
    sampler: str | None = None
    scheduler: str | None = None
    image: str | None = None
    mask: str | None = None
    refs: list[str] | None = None
    mode: str | None = None
    denoise: float | None = None
    lora: str | None = None
    lora_strength: float | None = None
    upscale: bool | None = None
    upscale_factor: float | None = None
    detail: float | None = None


class VideoReq(BaseModel):
    prompt: str
    negative: str | None = None
    preset: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    frames: int | None = None
    fps: int | None = None
    seed: int | None = -1
    cfg: float | None = None
    image: str | None = None
    image_strength: float | None = None
    audio: bool | None = None
    two_stage: bool | None = None
    model: str | None = None          # "ltx25" (default) | "hunyuan15"
    hy_preset: str | None = None      # Hunyuan: "fast" (4-step LoRA) | "balanced"
    auto_duration: bool | None = None


class VoiceReq(BaseModel):
    text: str
    engine: str | None = None
    language: str = "en"
    preset: str | None = None
    voice_id: int | None = None
    ref_audio: str | None = None
    ref_text: str | None = None
    speaker: str | None = None
    instruct: str | None = None
    speed: float = 1.0
    seed: int | None = None
    params: dict | None = None


def _dump(r: BaseModel):
    return {k: v for k, v in r.model_dump().items() if v is not None}


@app.post("/api/image/generate")
def gen_image(r: ImageReq):
    if not r.prompt.strip() and not r.image:
        raise HTTPException(400, "Please enter a prompt.")
    return jobs.submit("image", _dump(r)).to_dict()


@app.post("/api/video/generate")
def gen_video(r: VideoReq):
    if not r.prompt.strip():
        raise HTTPException(400, "Please enter a prompt.")
    return jobs.submit("video", _dump(r)).to_dict()


@app.post("/api/voice/generate")
def gen_voice(r: VoiceReq):
    if not r.text.strip():
        raise HTTPException(400, "Please enter some text.")
    if len(r.text) > 5000:
        raise HTTPException(400, "Text is too long (max 5000 characters per generation).")
    if r.engine and r.engine not in ("auto", "chatterbox", "qwen", "omnivoice", "fish"):
        raise HTTPException(400, "Unknown voice engine.")
    return jobs.submit("voice", _dump(r)).to_dict()


import clips  # noqa: E402
import dubbing  # noqa: E402
import youtube  # noqa: E402

import avatar  # noqa: E402

import fetch  # noqa: E402
import video_editor  # noqa: E402

jobs.EXTRA_RUNNERS.update({"avatar": avatar.run_avatar, "dub_line": dubbing.run_dub_line, "fetch_url": fetch.run_fetch_url,
                           **video_editor.EXTRA_RUNNERS})


@app.get("/api/editor/projects")
def list_editor_projects():
    projects = []
    for path in EDITOR_PROJECTS_DIR.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            projects.append(_editor_project_summary(path, value))
        except (OSError, ValueError, TypeError):
            log.warning("Skipping unreadable editor project %s", path.name)
    return sorted(projects, key=lambda item: item.get("updated_at") or "", reverse=True)


@app.get("/api/editor/projects/{project_id}")
def get_editor_project(project_id: str):
    path = _editor_project_path(project_id)
    if not path.is_file():
        raise HTTPException(404, "Project not found.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(500, "Saved project file is unreadable.")
    return value


@app.put("/api/editor/projects/{project_id}")
def save_editor_project(project_id: str, body: dict):
    path = _editor_project_path(project_id)
    name = str(body.get("name") or "").strip()[:120]
    document = body.get("document")
    if not name or not isinstance(document, dict) or document.get("version") != 2 or not isinstance(document.get("state"), dict):
        raise HTTPException(400, "A named version 2 editor project is required.")
    encoded = json.dumps({"name": name, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "document": document}, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 64 * 1024 * 1024:
        raise HTTPException(413, "Project file is larger than 64 MB.")
    temporary = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        log.exception("Could not save editor project %s", project_id)
        raise HTTPException(500, f"Could not save project: {exc}")
    return _editor_project_summary(path, json.loads(encoded))


@app.post("/api/editor/media/check")
def check_editor_media(body: dict):
    paths = body.get("paths")
    if not isinstance(paths, list) or len(paths) > 200:
        raise HTTPException(400, "Provide at most 200 media paths.")
    result = []
    for value in paths:
        path = str(value or "")
        try:
            resolved = jobs.resolve_input(path)
            result.append({"path": path, "exists": bool(resolved and resolved.is_file())})
        except (ValueError, OSError):
            result.append({"path": path, "exists": False})
    return {"items": result}


@app.post("/api/editor/assist")
async def editor_assist(body: dict):
    try:
        return await video_editor.assist(body.get("prompt", ""), body.get("context", {}))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/avatars")
def avatars_list():
    out = db.list_avatars()
    for a in out:
        a["image_url"] = file_url(a["image_path"])
        a["video_url"] = file_url(a["video_path"]) if a.get("video_path") else None
        a["preview_url"] = file_url(a["preview_path"]) if a.get("preview_path") else None
    return {"avatars": out, "infinitetalk": avatar.infinitetalk_installed()}


def _store_avatar_file(path: str, name: str) -> str:
    """Keep one copy: uploads are moved into data/avatars, existing outputs are referenced in place."""
    src = jobs.resolve_input(path)
    if str(src).lower().startswith(str(UPLOADS).lower()):
        dst = avatar.AVATARS / f"{_slug(name)}_{uuid.uuid4().hex[:6]}{src.suffix.lower()}"
        shutil.move(str(src), dst)
        return jobs.rel(dst)
    return jobs.rel(src)


@app.post("/api/avatars")
def avatars_create(body: dict):
    if not body.get("name") or not (body.get("image") or body.get("video")):
        raise HTTPException(400, "Name and a portrait image or video are required.")
    video_rel = _store_avatar_file(body["video"], body["name"]) if body.get("video") else None
    if body.get("image"):
        image_rel = _store_avatar_file(body["image"], body["name"])
    else:
        vid = ROOT / video_rel
        img = avatar.AVATARS / f"{_slug(body['name'])}_{uuid.uuid4().hex[:6]}.png"
        avatar.frame_at(vid, min(1.0, media_probe(vid)["duration"] / 2), img)
        image_rel = jobs.rel(img)
    aid = db.add_avatar(name=body["name"].strip(), image_path=image_rel, video_path=video_rel,
                        voice_id=body.get("voice_id"), language=body.get("language"), notes=body.get("notes"))
    return db.get_avatar(aid)


def media_probe(p):
    import media
    return media.probe(p)


@app.patch("/api/avatars/{aid}")
def avatars_update(aid: int, body: dict):
    db.update_avatar(aid, **{k: v for k, v in body.items() if k in ("name", "voice_id", "language", "notes")})
    return db.get_avatar(aid)


@app.delete("/api/avatars/{aid}")
def avatars_delete(aid: int):
    a = db.get_avatar(aid)
    if not a:
        raise HTTPException(404, "not found")
    db.delete_avatar(aid)
    for k in ("image_path", "video_path"):
        p = (ROOT / a[k]).resolve() if a.get(k) else None
        if p and str(p).lower().startswith(str(avatar.AVATARS).lower()):
            p.unlink(missing_ok=True)
    return {"ok": True}


jobs.EXTRA_RUNNERS.update({"dub_analyze": dubbing.run_dub_analyze, "dub_render": dubbing.run_dub_render,
                           "clips_analyze": clips.run_clips_analyze, "clips_export": clips.run_clips_export,
                           "clips_preview": clips.run_clips_preview, "clips_previews": clips.run_clips_previews,
                           "clips_enrich": clips.run_clips_enrich, "video_analyze": clips.run_video_analyze,
                           "youtube_upload": youtube.run_upload})


@app.get("/api/youtube/status")
def youtube_status():
    return youtube.status()


@app.get("/api/youtube/published")
def youtube_published():
    return youtube.published_uploads()


@app.post("/api/youtube/client")
def youtube_client(body: dict):
    try:
        return youtube.save_client(body)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/youtube/connect")
async def youtube_connect():
    try:
        return await youtube.start_connect()
    except (ValueError, RuntimeError, OSError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/youtube/disconnect")
def youtube_disconnect():
    return youtube.disconnect()


@app.get("/api/dub/{pid}")
def dub_project(pid: str):
    try:
        p = dubbing.load_project(pid)
    except FileNotFoundError:
        raise HTTPException(404, "Dub project not found.")
    for s in p["speakers"]:
        s["ref_url"] = file_url(jobs.rel(Path(s["ref"]))) if s.get("ref") and Path(s["ref"]).exists() else None
    return {k: v for k, v in p.items() if k not in ("audio",)} | {
        "segments": [{k: v for k, v in s.items() if k != "words"} for s in p["segments"]]}


@app.get("/api/clips/{pid}")
def clips_project(pid: str):
    try:
        p = clips.load(pid)
    except FileNotFoundError:
        raise HTTPException(404, "Clips project not found.")
    return p


@app.patch("/api/clips/{pid}/{cid}")
def clips_update(pid: str, cid: str, patch: dict):
    p = clips.load(pid)
    c = next((x for x in p["clips"] if x["id"] == cid), None)
    if not c:
        raise HTTPException(404, "Clip not found.")
    for k in ("status", "title", "start", "end", "words", "cuts", "analysis", "social"):
        if k in patch:
            c[k] = patch[k]
    clips.save(p)
    return c


@app.patch("/api/clips/{pid}")
def clips_project_update(pid: str, patch: dict):
    p = clips.load(pid)
    if "headline" in patch:
        p["headline"] = bool(patch["headline"])
    clips.save(p)
    return {"id": pid, "headline": p.get("headline", True)}


@app.post("/api/clips/{pid}/{cid}/duplicate")
def clips_duplicate(pid: str, cid: str):
    import copy
    p = clips.load(pid)
    src = next((x for x in p["clips"] if x["id"] == cid), None)
    if not src:
        raise HTTPException(404, "Clip not found.")
    dup = copy.deepcopy(src)
    n = 1 + max(int(x["id"].lstrip("c").split("_")[0]) for x in p["clips"] if x["id"].lstrip("c").split("_")[0].isdigit())
    dup.update(id=f"c{n}", status="candidate", exports=[], label=src["label"] + " (copy)")
    p["clips"].insert(p["clips"].index(src) + 1, dup)
    clips.save(p)
    return dup


@app.get("/api/clips-projects")
def clips_projects():
    out = []
    for d in sorted(clips.CLIPS.glob("*/project.json"), key=lambda f: -f.stat().st_mtime)[:20]:
        try:
            p = json.loads(d.read_text(encoding="utf-8"))
            out.append({"id": p["id"], "video": p["video_rel"], "clips": len(p["clips"]), "created": p["created"]})
        except Exception:
            pass
    return out


TOOL_REQUIRED = {"image_edit": ["prompt"], "image_upscale": ["image"], "remove_bg": ["image"],
                 "video_upscale": ["video"], "interpolate": ["video"], "video_edit": ["video", "prompt"],
                 "audio_sfx": ["prompt"],
                 "dub_analyze": ["video", "target_language"], "dub_render": ["project"],
                 "clips_analyze": ["video"], "clips_export": ["project", "clip"], "clips_preview": ["project", "clip"], "clips_previews": ["project"],
                 "clips_enrich": ["project", "clip"], "video_analyze": ["video"],
                 "youtube_upload": ["file", "title"],
                 "avatar": [], "dub_line": ["project"], "fetch_url": ["url"]}


@app.post("/api/tools/{kind}")
def run_tool(kind: str, body: dict):
    """Image Edit / Upscale / Remove BG and Video Edit / Upscale / Interpolate (and feature modules)."""
    if kind not in TOOL_REQUIRED and kind not in jobs.EXTRA_RUNNERS:
        raise HTTPException(404, "Unknown tool.")
    for field in TOOL_REQUIRED.get(kind, []):
        if not str(body.get(field) or "").strip():
            raise HTTPException(400, {"prompt": "Please enter an instruction.", "image": "Please add an image.",
                                      "video": "Please add a video.", "url": "Please paste a link."}.get(field, f"Missing {field}."))
    return jobs.submit(kind, body).to_dict()


@app.get("/api/jobs")
def list_jobs():
    return sorted((j.to_dict() for j in jobs.JOBS.values()), key=lambda j: -j["created"])[:50]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = jobs.JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j.to_dict()


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    return {"ok": await jobs.cancel(job_id)}


# ───────────────────────────── files ─────────────────────────────
@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "file").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".mp4",
                   ".webm", ".mov"):
        raise HTTPException(400, "Unsupported file type.")
    dst = UPLOADS / f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{ext}"
    with open(dst, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    rel = jobs.rel(dst)
    return {"path": rel, "url": file_url(rel), "name": file.filename}


def _safe_path(rel: str) -> Path:
    p = (ROOT / rel).resolve()
    if not any(str(p).lower().startswith(str(r.resolve()).lower()) for r in SERVABLE_ROOTS):
        raise HTTPException(403, "forbidden")
    if not p.exists():
        raise HTTPException(404, "file not found")
    return p


@app.get("/api/files/{rel:path}")
def get_file(rel: str, download: bool = False):
    p = _safe_path(rel)
    return FileResponse(p, filename=p.name if download else None)


@app.get("/api/mp3/{rel:path}")
def get_mp3(rel: str):
    p = _safe_path(rel)
    mp3 = jobs.to_mp3(p)
    return FileResponse(mp3, filename=mp3.name, media_type="audio/mpeg")


class PathReq(BaseModel):
    path: str


@app.post("/api/open-folder")
def open_folder(r: PathReq):
    p = (ROOT / r.path).resolve() if not Path(r.path).is_absolute() else Path(r.path).resolve()
    if not str(p).lower().startswith(str(ROOT).lower()):
        raise HTTPException(403, "forbidden")
    if p.is_file():
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        p.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(p)])
    return {"ok": True}


@app.post("/api/video/last-frame")
def last_frame(r: PathReq):
    src = jobs.resolve_input(r.path)
    out = jobs.extract_frame(src, last=True)
    rel = jobs.rel(out)
    return {"path": rel, "url": file_url(rel)}


# ───────────────────────────── history ─────────────────────────────
@app.get("/api/history")
def history(type: str | None = None, q: str | None = None, limit: int = 100, offset: int = 0):
    rows = db.list_generations(type, q, limit, offset)
    for r in rows:
        outs = r.get("outputs") or []
        r["urls"] = [file_url(o) for o in outs]
        r["exists"] = [(ROOT / o).exists() for o in outs]
    return rows


@app.delete("/api/history/{gid}")
def delete_history(gid: int, delete_files: bool = True):
    g = db.get_generation(gid)
    if not g:
        raise HTTPException(404, "not found")
    removed = []
    if delete_files:
        for o in g.get("outputs") or []:
            p = (ROOT / o).resolve()
            if str(p).lower().startswith(str(settings.OUTPUTS).lower()) and p.exists():
                p.unlink()
                removed.append(o)
                mp3 = p.with_suffix(".mp3")
                if mp3.exists():
                    mp3.unlink()
    db.delete_generation(gid)
    return {"ok": True, "removed": removed}


# ───────────────────────────── voices ─────────────────────────────
class VoiceIn(BaseModel):
    name: str
    ref_path: str
    language: str | None = None
    transcript: str | None = None
    notes: str | None = None


@app.get("/api/voices")
def voices():
    out = db.list_voices()
    for v in out:
        v["url"] = file_url(v["ref_path"])
    return out


@app.post("/api/voices")
async def create_voice(v: VoiceIn):
    src = jobs.resolve_input(v.ref_path)
    # Voices are stored once in voices/ (moved from uploads, or referenced if already an output/voice file).
    if not str(src).lower().startswith(str(VOICES).lower()):
        wav = jobs.ensure_wav(src)
        dst = VOICES / f"{_slug(v.name)}_{uuid.uuid4().hex[:6]}.wav"
        if str(src).lower().startswith(str(UPLOADS).lower()):
            shutil.move(str(wav), dst)          # uploads are temporary: move, no duplicate
            if wav != src and src.exists():
                src.unlink()
        else:
            shutil.copy2(wav, dst)              # generated outputs stay in history
        src = dst
    vid = db.add_voice(v.name.strip(), jobs.rel(src), v.language, v.transcript, v.notes)
    if not v.transcript:
        asyncio.create_task(_auto_transcribe(vid))
    return db.get_voice(vid)


def _slug(s):
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s)[:40] or "voice"


async def _auto_transcribe(vid: int):
    """Uses OmniVoice's Whisper to transcribe the reference (improves Qwen3-TTS cloning)."""
    v = db.get_voice(vid)
    svc = services.SERVICES["omnivoice"]
    if not v or svc.status != "online" or jobs.CURRENT["job"] is not None:
        return
    try:
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(svc.url + "/transcribe", json={"path": str(ROOT / v["ref_path"])})
        if r.status_code == 200 and r.json().get("text"):
            db.update_voice(vid, transcript=r.json()["text"].strip())
    except Exception:
        log.warning("auto-transcribe failed for voice %s", vid)


class VoicePatch(BaseModel):
    name: str | None = None
    language: str | None = None
    transcript: str | None = None
    notes: str | None = None


@app.patch("/api/voices/{vid}")
def patch_voice(vid: int, v: VoicePatch):
    db.update_voice(vid, **{k: val for k, val in v.model_dump().items() if val is not None})
    return db.get_voice(vid)


@app.post("/api/voices/{vid}/transcribe")
async def transcribe_voice(vid: int):
    await _auto_transcribe(vid)
    return db.get_voice(vid)


@app.delete("/api/voices/{vid}")
def delete_voice(vid: int):
    v = db.get_voice(vid)
    if not v:
        raise HTTPException(404, "not found")
    db.delete_voice(vid)
    p = ROOT / v["ref_path"]
    if str(p.resolve()).lower().startswith(str(VOICES).lower()) and not db.voice_ref_in_use(v["ref_path"], vid):
        p.unlink(missing_ok=True)
    return {"ok": True}


# ───────────────────────────── models ─────────────────────────────
@app.get("/api/models")
def models():
    return catalog.listing(resources.state["loaded"])


@app.post("/api/models/{model_id}/install")
def model_install(model_id: str):
    model = next((m for m in catalog.CATALOG if m["id"] == model_id), None)
    if not model or not model.get("installable"):
        raise HTTPException(404, "This model does not have an automatic installer.")
    return jobs.submit("model_install", {"model_id": model_id}).to_dict()


@app.post("/api/models/{model_id}/load")
async def model_load(model_id: str):
    m = next((x for x in catalog.CATALOG if x["id"] == model_id), None)
    if not m:
        raise HTTPException(404, "unknown model")
    if m["family"] == "comfyui":
        # ComfyUI loads on demand; a tiny warm-up generation pre-loads image models.
        if m["type"] == "image" and m["id"] != "realesrgan_x4":
            job = jobs.submit("image", {"prompt": "warm-up", "model": m["id"], "width": 256, "height": 256,
                                        "steps": 1, "preset": "fast", "_warmup": True})
            return {"ok": True, "job": job.to_dict()}
        return {"ok": True, "message": "Loaded automatically when you generate."}
    svc = services.SERVICES[m["backend"]]
    await resources.prepare(m["family"], f"voice:{m['family']}")
    async with httpx.AsyncClient(timeout=600) as c:
        r = await c.post(svc.url + "/load")
    if r.status_code == 200:
        resources.mark_loaded(m["family"], svc.label)
    return r.json()


@app.post("/api/models/{model_id}/unload")
async def model_unload(model_id: str):
    m = next((x for x in catalog.CATALOG if x["id"] == model_id), None)
    if not m:
        raise HTTPException(404, "unknown model")
    await resources.unload_family(m["family"])
    return {"ok": True}


@app.post("/api/models/unload-all")
async def unload_everything():
    await resources.unload_all()
    return {"ok": True, "gpu": gpu.gpu_stats()}


@app.post("/api/cleanup/ram")
async def cleanup_ram():
    return await cleanup.free_ram()


@app.get("/api/cleanup/temp")
async def cleanup_temp_info():
    return await asyncio.to_thread(cleanup.temp_info)


@app.post("/api/cleanup/temp")
async def cleanup_temp():
    return await asyncio.to_thread(cleanup.clean_temp)


@app.delete("/api/models/{model_id:path}")
async def model_delete(model_id: str, confirm: str = ""):
    if confirm != model_id:
        raise HTTPException(400, "Deletion must be confirmed by sending confirm=<model id>.")
    files = catalog.files_to_delete(model_id)
    if not files:
        raise HTTPException(404, "unknown model")
    await resources.unload_all()
    removed = []
    for f in files:
        f = f.resolve()
        if not str(f).lower().startswith(str(MODELS).lower()):
            continue
        if f.is_dir():
            shutil.rmtree(f)
            removed.append(str(f))
        elif f.exists():
            f.unlink()
            removed.append(str(f))
    log.warning("deleted model %s: %s", model_id, removed)
    return {"ok": True, "removed": removed}


# ───────────────────────────── logs ─────────────────────────────
@app.get("/api/logs")
def list_logs():
    return sorted(p.name for p in LOGS.glob("*.log"))


@app.get("/api/logs/{name}")
def read_log(name: str, lines: int = 300):
    p = (LOGS / name).resolve()
    if p.parent != LOGS.resolve() or not p.exists():
        raise HTTPException(404, "log not found")
    with open(p, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 400_000))
        data = f.read().decode("utf-8", errors="replace")
    return {"name": name, "text": "\n".join(data.splitlines()[-lines:])}


@app.post("/api/shutdown")
async def shutdown():
    """Graceful stop: cancel running work, stop backends, then exit the orchestrator."""
    cur = jobs.CURRENT["job"]
    if cur:
        await jobs.cancel(cur.id)

    async def bye():
        await asyncio.sleep(0.3)
        await services.stop_all()
        (settings.DATA / "api.pid").unlink(missing_ok=True)
        log.info("LocalAI stopped")
        os._exit(0)

    asyncio.create_task(bye())
    return {"ok": True}


@app.exception_handler(Exception)
async def unhandled(request, exc):
    log.exception("unhandled error on %s", request.url.path)
    human, _ = jobs.humanize(exc)
    return JSONResponse({"detail": human}, status_code=500)


# ───────────────────────────── UI (static) ─────────────────────────────
class SPAStatic(StaticFiles):
    async def get_response(self, path, scope):
        try:
            resp = await super().get_response(path, scope)
            if resp.status_code != 404:
                return resp
        except Exception:
            pass
        return await super().get_response("index.html", scope)


if UI_DIST.exists():
    app.mount("/", SPAStatic(directory=str(UI_DIST), html=True), name="ui")


# ───────────────────────────── lifecycle ─────────────────────────────
async def lifecycle():
    db.init()
    jobs.QUEUE = asyncio.Queue()
    asyncio.create_task(jobs.worker())
    await services.start_all()
    asyncio.create_task(services.monitor_loop())


async def serve():
    setup_logging()
    ports = settings.load()["ports"]
    for key in ("api", "ui"):
        if services._port_busy(ports[key]):
            try:
                async with httpx.AsyncClient(timeout=2) as c:
                    if (await c.get(f"http://127.0.0.1:{ports[key]}/api/health")).status_code == 200:
                        log.error("LocalAI is already running on port %s", ports[key])
                        return
            except Exception:
                pass
            settings.RUNTIME_PORTS[key] = services._free_port(ports[key] + 1)
            log.warning("port %s busy, using %s", ports[key], settings.RUNTIME_PORTS[key])
    (settings.DATA / "ports.json").write_text(
        __import__("json").dumps({k: settings.port(k) for k in ports}), encoding="utf-8")
    (settings.DATA / "api.pid").write_text(str(os.getpid()))
    await lifecycle()
    servers =[uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.port(k), log_level="warning",
                                             access_log=False)) for k in ("ui", "api")]
    log.info("LocalAI Studio: UI http://127.0.0.1:%s  API http://127.0.0.1:%s", settings.port("ui"),
             settings.port("api"))
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(serve())
