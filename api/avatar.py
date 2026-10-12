"""Talking avatars and face-preserving lip sync (HeyGen-style), fully local.

Photo avatar  : portrait + (script -> TTS | audio) -> talking video
                balanced/quality: InfiniteTalk (audio-driven lip sync, chunked at 25 fps)
                fast:             LTX-2.5 image+audio (quick, looser mouth timing)
Video avatar  : same, using a frame of the person's own video as the start image (+ optional voice clone
                from that video's speech)
Lip sync      : existing video + new speech -> InfiniteTalk per chunk, then only the mouth/jaw region is
                warped back onto the ORIGINAL frames (identity, eyes, hair, background, head motion kept)
"""
import logging
import math
import shutil
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

import comfy
import db
import media
import resources
import workflows
import workflows_tools as wt
from dubbing import best_reference, build_utterances, load_mono, media_call, pick_engine, tts
from jobs import _progress_cb, _sampled, next_output_path, rel, resolve_input
from settings import DATA, MODELS, OUT_AVATAR, OUTPUTS, ROOT

log = logging.getLogger("avatar")
AVATARS = DATA / "avatars"
AVATARS.mkdir(parents=True, exist_ok=True)
OUT_AVATAR.mkdir(parents=True, exist_ok=True)
WORK = ROOT / "temp" / "avatar"
WORK.mkdir(parents=True, exist_ok=True)
MOVEMENT = {"calm": "Minimal head movement, calm and composed, steady gaze into the camera.",
            "natural": "Natural subtle head movement, natural blinking, relaxed expression.",
            "expressive": "Expressive face and lively head movement with natural hand gestures."}
QUALITY = {"fast": {"engine": "ltx", "steps": 0}, "balanced": {"engine": "infinitetalk", "steps": 6},
           "quality": {"engine": "infinitetalk", "steps": 8}}
RES = {"480p": 480, "720p": 720}


def infinitetalk_installed() -> bool:
    need = ["video/diffusion_models/" + wt.INFTALK["unet"], "video/model_patches/" + wt.INFTALK["patch"],
            "video/audio_encoders/" + wt.INFTALK["audio_enc"], "video/text_encoders/" + wt.INFTALK["clip"]]
    return all((MODELS / n).exists() for n in need)


def dims_for(w: int, h: int, short: int = 480) -> tuple[int, int]:
    """Keep the source aspect ratio, short side `short`, multiples of 16."""
    if w >= h:
        return int(round(w * short / h / 16)) * 16, short
    return short, int(round(h * short / w / 16)) * 16


def frame_at(video, t, dst) -> Path:
    media.run(["-ss", f"{max(0.0, t):.3f}", "-i", video, "-frames:v", 1, dst])
    return Path(dst)


# ─────────────────────────── InfiniteTalk chunk loop ───────────────────────────
async def _mouth_still(video: Path, audio: Path) -> bool:
    """True when the mouth hardly moves although the chunk contains speech (spread of mouth opening < 0.025;
    measured: broken run 0.012, normal runs 0.05-0.07)."""
    try:
        a, _ = sf.read(str(audio), dtype="float32")
        if 20 * np.log10(float(np.sqrt(np.mean(np.square(a)))) + 1e-9) < -40:
            return False                                   # (almost) silent chunk: a still mouth is correct
        tr = await media_call("/track", {"path": str(video), "start": 0.0, "end": 3.2, "sample_fps": 12})
        mouth = sorted(max(f["mouth"] for f in s["faces"]) for s in tr["samples"] if s["faces"])
        if len(mouth) < 10:
            return False
        spread = mouth[int(len(mouth) * 0.9)] - mouth[int(len(mouth) * 0.1)]
        log.info("InfiniteTalk first chunk mouth spread %.4f", spread)
        return spread < 0.025
    except Exception as e:                                 # the check must never break a generation
        log.warning("mouth check skipped: %s", e)
        return False


async def infinitetalk(job, start_image: Path, audio: Path, width: int, height: int, prompt: str, steps: int,
                       seed: int, work: Path, frame_source=None, progress=(0.1, 0.8)) -> Path:
    """Generate a lip-synced video for the full audio in 81-frame chunks (25 fps) with 9-frame continuation.
    frame_source(t) -> image path: optional per-chunk start image (for existing-video lip sync)."""
    fps, L, MF = wt.INFTALK_FPS, 81, wt.MOTION_FRAMES
    dur = media.probe(audio)["duration"]
    total = int(math.ceil(dur * fps)) + 1
    wav = work / "speech16k.wav"
    media.run(["-i", audio, "-ac", "1", "-ar", 16000, wav])
    chunks, done, prev_tail, k = [], 0, None, 0
    nchunks = max(1, math.ceil((total - MF) / (L - MF)))
    while done < total:
        if job.cancel_requested:
            raise RuntimeError("Generation was cancelled.")
        first = done - (MF if prev_tail else 0)          # chunk's first frame (incl. motion frames)
        a_start = first / fps
        seg = work / f"a_{k:03d}.wav"
        media.run(["-ss", f"{a_start:.3f}", "-t", f"{(L + 4) / fps:.3f}", "-i", wav, "-af", f"apad=whole_dur={(L + 4) / fps:.3f}",
                   seg])
        img = frame_source(done / fps) if frame_source else start_image
        graph = wt.build_infinitetalk_chunk({"prompt": prompt, "steps": steps, "seed": seed + k},
                                            comfy.stage_input(img), comfy.stage_input(seg),
                                            comfy.stage_input(prev_tail) if prev_tail else None, width, height, L)
        base = progress[0] + (progress[1] - progress[0]) * k / nchunks

        def cb(frac, v, m, node, _b=base):
            _progress_cb(job, _b, (progress[1] - progress[0]) / nchunks)(frac, v, m, node)
            job.message = f"Lip sync chunk {k + 1}/{nchunks} · " + job.message.split(" · ")[-1]
        files = await comfy.run(graph, cb, timeout=3600)
        vid = next(f for f in files if f.suffix.lower() == ".mp4")
        out = work / f"c_{k:03d}.mp4"
        shutil.move(str(vid), out)
        if k == 0 and not frame_source and await _mouth_still(out, seg):
            # On a cold model load ComfyUI can occasionally run the model without the InfiniteTalk patch
            # (the mouth then barely moves). Reload everything once and redo the chunk.
            job.message = "Lip sync looked weak; reloading the model and retrying…"
            await comfy.free(unload_models=True)
            files = await comfy.run(graph, cb, timeout=3600)
            vid = next(f for f in files if f.suffix.lower() == ".mp4")
            shutil.move(str(vid), out)
        keep_from = MF if prev_tail else 0
        chunks.append((out, keep_from))
        done = first + L
        tail = work / f"tail_{k:03d}.mp4"
        media.run(["-sseof", f"-{(MF + 0.5) / fps:.3f}", "-i", out, "-frames:v", MF, "-c:v", "libx264", "-crf", "8",
                   "-pix_fmt", "yuv420p", tail])
        prev_tail, k = tail, k + 1
    # join (drop the 9 overlapping motion frames of continuation chunks) and cut to the audio length
    joined = work / "talk_joined.mp4"
    media.concat_with_audio([c for c, _ in chunks], joined, drop_first_frame_after_first=False, fps=fps)
    if any(kf for _, kf in chunks):
        parts, fc = [], []
        for i, (c, kf) in enumerate(chunks):
            fc.append(f"[{i}:v]trim=start_frame={kf},setpts=PTS-STARTPTS[v{i}]")
            parts += ["-i", c]
        fc.append("".join(f"[v{i}]" for i in range(len(chunks))) + f"concat=n={len(chunks)}:v=1:a=0[v]")
        media.run([*parts, "-filter_complex", ";".join(fc), "-map", "[v]", "-t", f"{dur:.3f}", "-r", fps,
                   "-c:v", "libx264", "-crf", "12", "-preset", "medium", "-pix_fmt", "yuv420p", joined])
    return joined


# ─────────────────────────── voice for the avatar ───────────────────────────
async def speak(job, p: dict, work: Path, clone_from: Path | None = None) -> tuple[Path, dict]:
    """Script -> speech wav using the existing TTS engines (saved / cloned / uploaded / AI voice)."""
    if p.get("audio"):
        src = resolve_input(p["audio"])
        out = work / "speech.wav"
        media.run(["-i", src, "-ac", "1", "-ar", 24000, out])
        return out, {"voice": "uploaded audio"}
    lang = p.get("language", "en")
    engine = pick_engine(lang, p.get("engine"))
    mode = p.get("voice", "saved" if p.get("voice_id") else "ai")
    ref = ref_text = None
    if mode == "saved" and p.get("voice_id"):
        v = db.get_voice(int(p["voice_id"]))
        if v:
            ref, ref_text = str(ROOT / v["ref_path"]), v.get("transcript")
    elif mode == "upload" and p.get("ref_audio"):
        ref = str(resolve_input(p["ref_audio"]))
    elif mode == "ai":
        # built-in default voice for the language (Default female/male (RU) in the voice library)
        want = "female" if p.get("ai_voice", "female") == "female" else "male"
        for v in db.list_voices():
            if v["name"].lower().startswith(f"default {want}"):     # cloning works across languages
                ref, ref_text = str(ROOT / v["ref_path"]), v.get("transcript")
                break
    elif mode == "clone" and clone_from:
        job.message = "Extracting the original voice…"
        a = work / "orig_audio.wav"
        media.run(["-i", clone_from, "-vn", "-ac", "2", "-ar", 48000, a])
        stems = await media_call("/separate", {"path": str(a), "out_dir": str(work)})
        tr = await media_call("/transcribe", {"path": stems["vocals"]})
        utts = build_utterances(tr["segments"])
        ref, ref_text = best_reference(utts, "SPEAKER_00", stems["vocals"], work / "clone_ref.wav") if utts else (None, None)
        await resources.unload_family("media")
    await resources.prepare(engine, f"voice:{engine}")
    params = {}
    if engine == "chatterbox":
        params["exaggeration"] = {"calm": 0.4, "neutral": 0.5, "warm": 0.55, "excited": 0.8,
                                  "serious": 0.45}.get(p.get("emotion", "neutral"), 0.5)
        params["cfg_weight"] = 1.0 - float(p.get("similarity", 0.5)) * 0.6 if ref else 0.5
    out = work / "speech.wav"
    job.message = "Generating voice…"
    await tts(engine, p["script"], lang, ref, ref_text, out, params, speed=float(p.get("speed", 1.0)))
    if abs(float(p.get("speed", 1.0)) - 1) > 0.02 and engine != "omnivoice":
        from jobs import apply_tempo
        apply_tempo(out, float(p["speed"]))
    await resources.unload_family(engine)
    return out, {"voice": mode, "engine": engine}


# ─────────────────────────── jobs ───────────────────────────
async def run_avatar(job):
    p = dict(job.params)
    work = WORK / uuid.uuid4().hex[:8]
    work.mkdir(parents=True)
    t0 = time.time()
    av = db.get_avatar(int(p["avatar_id"])) if p.get("avatar_id") else None
    video = resolve_input(p["video"]) if p.get("video") else (ROOT / av["video_path"] if av and av.get("video_path") else None)
    if p.get("image"):
        image = resolve_input(p["image"])
    elif av:
        image = ROOT / av["image_path"]
    elif video:
        image = frame_at(video, min(1.0, media.probe(video)["duration"] / 2), work / "start.png")
    else:
        raise ValueError("Please add a portrait photo, a video or choose a saved avatar.")
    if av and not p.get("voice_id") and av.get("voice_id") and p.get("voice") in (None, "", "saved"):
        p["voice_id"], p["voice"] = av["voice_id"], "saved"

    job.message, job.progress = "Preparing…", 0.02
    await resources.unload_family("comfyui")        # no LTX / image model while voices are generated
    speech, vinfo = await speak(job, p, work, clone_from=video)
    dur = media.probe(speech)["duration"]
    q = QUALITY.get(p.get("quality", "balanced"), QUALITY["balanced"])
    use_it = q["engine"] == "infinitetalk" and infinitetalk_installed()
    prompt = (p.get("prompt") or "A person is talking to the camera.") + " " + MOVEMENT.get(p.get("movement", "natural"), "")
    if p.get("background_prompt"):
        prompt += " Background: " + p["background_prompt"]
    iw, ih = media.probe(image)["width"], media.probe(image)["height"]
    await resources.prepare("comfyui", "video:infinitetalk" if use_it else "video:ltx25",
                            notify=lambda m: setattr(job, "message", m))
    sample = {}
    if use_it:
        w, h = dims_for(iw, ih, 480)
        talk = await _sampled(infinitetalk(job, image, speech, w, h, prompt, q["steps"], int(p.get("seed", 7)), work),
                              sample)
        engine = "InfiniteTalk"
    else:
        short = RES.get(p.get("resolution", "480p"), 480)
        w, h = dims_for(iw, ih, max(512, short))
        graph, _ = workflows.build_avatar({"width": w, "height": h, "fps": 24, "duration": dur, "prompt": prompt,
                                           "seed": int(p.get("seed", 7))},
                                          {"image": comfy.stage_input(image), "audio": comfy.stage_input(speech)})
        files = await _sampled(comfy.run(graph, _progress_cb(job, 0.2, 0.7)), sample)
        talk = next(f for f in files if f.suffix == ".mp4")
        engine = "LTX-2.5" + ("" if q["engine"] == "ltx" else " (InfiniteTalk not installed)")
    resources.mark_loaded("comfyui", engine)
    final = talk
    if p.get("resolution") == "720p" and use_it:
        job.message, job.progress = "Upscaling to 720p…", 0.9
        up = work / "up.mp4"
        media.run(["-i", talk, "-vf", f"scale=-2:720:flags=lanczos", "-c:v", "libx264", "-crf", "16", up])
        final = up
    dst = next_output_path(OUT_AVATAR, "avatar", "mp4")
    media.run(["-i", final, "-i", speech, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
               "-b:a", "192k", "-shortest", "-movflags", "+faststart", dst])
    shutil.rmtree(work, ignore_errors=True)
    secs = round(time.time() - t0, 1)
    job.gen_id = db.add_generation(type="video", engine="comfyui", model=f"Avatar · {engine}",
                                   prompt=p.get("script") or "(audio)", params={**p, **vinfo, "engine": engine},
                                   inputs={"image": rel(image), "avatar": av and av["name"]}, outputs=[rel(dst)],
                                   status="done", seconds=secs, peak_vram_mb=sample.get("peak"))
    if av and not av.get("preview_path"):
        db.update_avatar(av["id"], preview_path=rel(dst))
    job.result = {"outputs": [rel(dst)], "seconds": secs, "engine": engine, "duration": round(dur, 2),
                  "peak_vram_mb": sample.get("peak")}
    await resources.after_job("comfyui")


async def lipsync_video(job, video: Path, speech_mix: Path, voice_only: Path, work: Path, quality: str,
                        progress=(0.1, 0.9)) -> Path:
    """Face-preserving lip sync of an existing video to new speech. Returns a video (no audio)."""
    info = media.probe(video)
    q = QUALITY.get(quality, QUALITY["balanced"])
    if not infinitetalk_installed():
        raise RuntimeError("Lip sync needs the InfiniteTalk model (not installed yet).")
    w, h = dims_for(info["width"], info["height"], 480)
    frames_dir = work / "src_frames"
    frames_dir.mkdir(exist_ok=True)

    def frame_source(t):
        t = min(t, max(0.0, info["duration"] - 0.1))
        return frame_at(video, t, frames_dir / f"f_{int(t * 1000):08d}.png")

    await resources.unload_all()
    await resources.prepare("comfyui", "video:infinitetalk", notify=lambda m: setattr(job, "message", m))
    talk = await infinitetalk(job, None, voice_only, w, h,
                              "A person is talking naturally, same person, same pose and framing.", q["steps"] or 6, 7,
                              work, frame_source=frame_source, progress=(progress[0], progress[1] - 0.1))
    await resources.unload_family("comfyui")
    # resample the generation to the original fps / duration, then paste back only the mouth region
    job.message = "Blending lips onto the original video…"
    gen = work / "gen_resampled.mp4"
    media.run(["-i", talk, "-vf", f"fps={info['fps']},scale={info['width']}:{info['height']}:flags=lanczos",
               "-c:v", "libx264", "-crf", "10", "-preset", "veryfast", "-pix_fmt", "yuv420p", gen])
    out = work / "lipsynced.mp4"
    r = await media_call("/mouth_blend", {"original": str(video), "generated": str(gen), "out": str(out)}, timeout=7200)
    log.info("mouth blend: %s", r)
    await resources.unload_family("media")
    return out
