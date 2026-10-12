"""Video dubbing: analyze (transcribe → speakers → translate) then render (TTS → timing → mix → subtitles → MP4).

Projects live in data/dub/<id>/project.json so the user can review/edit lines and voices before rendering.
"""
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

import captions
import db
import llm
import media
import resources
from jobs import next_output_path, rel, resolve_input, _sampled
from services import SERVICES
from settings import DATA, OUT_DUBBING, OUT_TRANSLATED, OUT_VIDEOS, OUTPUTS, ROOT

log = logging.getLogger("dub")
DUB = DATA / "dub"
DUB.mkdir(parents=True, exist_ok=True)
SR = 48000
ENGINE_LANGS = {
    "chatterbox": {"ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl", "no",
                   "pl", "pt", "ru", "sv", "sw", "tr", "zh"},
    "qwen": {"en", "ru", "de", "fr", "es", "it", "pt", "zh", "ja", "ko"},
    "omnivoice": None,  # 600+ languages
}
QUALITY = {  # max speed-up before we extend the timeline instead
    "fast": {"max_tempo": 1.35, "preset": "fast"},
    "balanced": {"max_tempo": 1.25, "preset": "natural"},
    "quality": {"max_tempo": 1.15, "preset": "max"},
}
FILLERS = {"um", "uh", "erm", "hmm", "эм", "ээ", "мм", "äh", "ähm", "hm"}


def project_path(pid):
    return DUB / pid / "project.json"


def load_project(pid) -> dict:
    return json.loads(project_path(pid).read_text(encoding="utf-8"))


def save_project(p: dict):
    project_path(p["id"]).write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


async def media_call(path, body, timeout=3600):
    svc = SERVICES["media"]
    if svc.status != "online" and not await svc.check():
        await svc.start()
        if not await svc.wait_online():
            raise RuntimeError("The media tools backend is offline.")
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(svc.url + path, json=body)
    data = r.json()
    if r.status_code != 200:
        err = RuntimeError(data.get("error", "media backend error"))
        err.code = data.get("code")
        raise err
    return data


ENGINE_LANGS["fish"] = {"ru", "en", "de", "fr", "es", "it", "pt", "zh", "ja", "ko", "pl", "tr", "nl", "uk", "ar"}
RANKINGS = ROOT / "config" / "tts_rankings.json"


def engine_ranking(lang: str) -> list[str]:
    """Measured ranking (written by the TTS benchmark); falls back to a sensible default order."""
    try:
        r = json.loads(RANKINGS.read_text(encoding="utf-8-sig"))
        if r.get(lang):
            return list(r[lang])
    except Exception:
        pass
    return ["chatterbox", "qwen", "omnivoice", "fish"]


def pick_engine(tgt: str, requested: str | None) -> str:
    if requested and requested != "auto":
        return requested
    for e in engine_ranking(tgt):
        svc = SERVICES.get(e)
        if svc is None or not svc.installed:
            continue
        if ENGINE_LANGS.get(e) is None or tgt in ENGINE_LANGS[e]:
            return e
    return "omnivoice"


def build_utterances(segments, turns=None, max_len=14.0, gap=0.7):
    """Split whisper words into sentence-like utterances, breaking at speaker changes, punctuation and pauses."""
    words = [w for s in segments for w in s["words"]] or [
        {"start": s["start"], "end": s["end"], "word": " " + s["text"]} for s in segments]

    def speaker_at(t):
        if not turns:
            return "SPEAKER_00"
        best, bd = turns[0]["speaker"], 1e9
        for tr in turns:
            if tr["start"] <= t <= tr["end"]:
                return tr["speaker"]
            d = min(abs(t - tr["start"]), abs(t - tr["end"]))
            if d < bd:
                best, bd = tr["speaker"], d
        return best

    utts, cur = [], []
    for w in words:
        spk = speaker_at((w["start"] + w["end"]) / 2)
        if cur and (spk != cur[-1]["spk"] or w["start"] - cur[-1]["end"] > gap or
                    w["end"] - cur[0]["start"] > max_len):
            utts.append(cur)
            cur = []
        cur.append({**w, "spk": spk})
        if w["word"].strip()[-1:] in ".!?…" and w["end"] - cur[0]["start"] > 1.2:
            utts.append(cur)
            cur = []
    if cur:
        utts.append(cur)
    out = []
    for u in utts:
        text = "".join(x["word"] for x in u).strip()
        if not text or all(x["word"].strip().lower().strip(".,!?") in FILLERS for x in u):
            continue
        out.append({"i": len(out), "start": u[0]["start"], "end": u[-1]["end"], "speaker": u[0]["spk"],
                    "text": text, "words": [{"start": x["start"], "end": x["end"], "word": x["word"]} for x in u]})
    return out


def cut_audio(src, start, end, dst, sr=24000):
    media.run(["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", src, "-ac", "1", "-ar", sr, dst])


def best_reference(utts, speaker, vocals, dst):
    """Longest clean stretch (5–12 s) of this speaker, used as the clone reference."""
    cands = [u for u in utts if u["speaker"] == speaker]
    if not cands:
        return None, ""
    cands.sort(key=lambda u: -min(u["end"] - u["start"], 12))
    u = cands[0]
    a, b = u["start"], min(u["end"], u["start"] + 12)
    # extend with the following utterance of the same speaker if the first is short
    if b - a < 5:
        nxt = [x for x in cands if x["start"] > u["end"] and x["start"] - u["end"] < 1.5]
        if nxt:
            b = min(nxt[0]["end"], a + 12)
    cut_audio(vocals, max(0, a - 0.05), b + 0.1, dst)
    text = " ".join(x["text"] for x in utts if x["speaker"] == speaker and x["start"] >= a and x["end"] <= b + 0.2)
    return str(dst), text or u["text"]


# ─────────────────────────── phase 1: analyze ───────────────────────────
async def run_dub_analyze(job):
    p = dict(job.params)
    src = resolve_input(p["video"])
    info = media.probe(src)
    if not info["has_audio"]:
        raise ValueError("This video has no audio track to dub.")
    pid = uuid.uuid4().hex[:10]
    work = DUB / pid
    work.mkdir(parents=True)
    note = lambda m: setattr(job, "message", m)

    # Lightweight models only: make sure LTX / image models are not holding the GPU.
    await resources.prepare("media", "media:dub", notify=note)
    await resources.unload_family("comfyui")

    job.message, job.progress = "Extracting audio…", 0.03
    audio = work / "audio.wav"
    media.run(["-i", src, "-vn", "-ac", "2", "-ar", SR, audio])

    stems = {}
    if p.get("preserve_background", True):
        job.message, job.progress = "Separating voice from background…", 0.08
        stems = await media_call("/separate", {"path": str(audio), "out_dir": str(work)})
    vocals = stems.get("vocals") or str(audio)

    job.message, job.progress = "Transcribing…", 0.25
    src_lang = p.get("source_language") if p.get("source_language") not in (None, "", "auto") else None
    tr = await media_call("/transcribe", {"path": vocals, "language": src_lang})
    lang = tr["language"]

    turns, spk_note = None, None
    mode = str(p.get("speakers", "auto"))
    if mode != "1":
        job.message, job.progress = "Detecting speakers…", 0.45
        try:
            d = await media_call("/diarize", {"path": vocals, "num_speakers": int(mode) if mode.isdigit() else None})
            turns = d["turns"] if len(d["speakers"]) > 1 else None
        except Exception as e:
            spk_note = ("Speaker detection needs the free pyannote license (see README); treated as one speaker."
                        if getattr(e, "code", "") == "gated" else f"Speaker detection skipped: {e}")
            log.warning(spk_note)
    utts = build_utterances(tr["segments"], turns)
    if not utts:
        raise ValueError("No speech was found in this video.")

    tgt = p["target_language"]
    job.message, job.progress = "Translating…", 0.55
    await resources.unload_family("media")          # free Whisper/Demucs before the LLM loads
    await llm.ensure(note)
    translations = await llm.translate([u["text"] for u in utts], lang, tgt,
                                       context=p.get("context", ""), notify=note,
                                       mode=p.get("translation_mode") or None)
    await resources.unload_family("llm")            # release the LLM's VRAM right away
    for u, t in zip(utts, translations):
        u["translation"] = t

    job.message, job.progress = "Preparing voices…", 0.9
    speakers = []
    for k, spk in enumerate(sorted({u["speaker"] for u in utts})):
        ref, ref_text = best_reference(utts, spk, vocals, work / f"ref_{k}.wav")
        speakers.append({"id": spk, "label": f"Speaker {k + 1}", "ref": ref, "ref_text": ref_text,
                         "voice": "clone", "voice_id": None, "custom_ref": None})

    proj = {"id": pid, "created": time.time(), "video": str(src), "video_rel": rel(src), "info": info,
            "source_language": lang, "target_language": tgt, "segments": utts, "speakers": speakers,
            "background": stems.get("background"), "vocals": vocals, "audio": str(audio),
            "engine": pick_engine(tgt, p.get("engine")), "quality": p.get("quality", "balanced"),
            "subtitles": p.get("subtitles", "off"), "note": spk_note}
    save_project(proj)
    job.result = {"project": pid, "segments": len(utts), "speakers": len(speakers), "language": lang,
                  "note": spk_note, "outputs": []}
    await resources.after_job("media")


# ─────────────────────────── single-line voice preview (review screen) ───────────────────────────
async def run_dub_line(job):
    """Generate one translated line with the chosen voice so the user can listen before rendering."""
    p = dict(job.params)
    proj = load_project(p["project"])
    seg = next(s for s in proj["segments"] if s["i"] == int(p["segment"]))
    text = p.get("text") or seg.get("translation")
    sp = next((s for s in proj["speakers"] if s["id"] == seg["speaker"]), {})
    if p.get("speaker_override"):
        sp = {**sp, **p["speaker_override"]}
    ref, ref_text = sp.get("ref"), sp.get("ref_text")
    if sp.get("voice") == "saved" and sp.get("voice_id"):
        v = db.get_voice(int(sp["voice_id"]))
        if v:
            ref, ref_text = str(ROOT / v["ref_path"]), v.get("transcript")
    elif sp.get("voice") == "custom" and sp.get("custom_ref"):
        ref, ref_text = str(resolve_input(sp["custom_ref"])), None
    elif sp.get("voice") == "ai":
        ref, ref_text = None, None
    engine = p.get("engine") or proj["engine"]
    await resources.prepare(engine, f"voice:{engine}")
    out_dir = DUB / proj["id"] / "lines"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"line_{seg['i']:04d}_{int(time.time())}.wav"
    params = {"cfg_weight": 0.2} if engine == "chatterbox" and proj["source_language"] != proj["target_language"] else {}
    r = await tts(engine, text, proj["target_language"], ref, ref_text, out, params)
    slot = seg["end"] - seg["start"]
    job.result = {"outputs": [rel(out)], "duration": round(r.get("duration", 0), 2), "slot": round(slot, 2),
                  "fits": r.get("duration", 0) <= slot * 1.25}


# ─────────────────────────── phase 2: render ───────────────────────────
async def tts(engine, text, lang, ref, ref_text, out, params, speed=1.0, normalize=True):
    if normalize and lang == "ru":
        import ru_text
        text = ru_text.normalize(text)        # numbers, dates, currency, abbreviations, pronunciations
    svc = SERVICES[engine]
    if svc.status != "online" and not await svc.check():
        await svc.start()
        await svc.wait_online()
    body = {"text": text, "out_path": str(out), "language": lang, "ref_audio": ref, "ref_text": ref_text,
            "speed": speed, "params": params}
    async with httpx.AsyncClient(timeout=900) as c:
        r = await c.post(svc.url + "/generate", json=body)
    if r.status_code != 200:
        raise RuntimeError(r.json().get("error", "voice generation failed"))
    return r.json()


def load_mono(path, sr=SR) -> np.ndarray:
    tmp = Path(path).with_suffix(".48k.wav")
    media.run(["-i", path, "-ac", "1", "-ar", sr, tmp])
    x, _ = sf.read(tmp, dtype="float32")
    tmp.unlink(missing_ok=True)
    return x


def trim_silence(x: np.ndarray, thr=0.01, pad=0.04) -> np.ndarray:
    idx = np.where(np.abs(x) > thr)[0]
    if len(idx) == 0:
        return x
    a, b = max(0, idx[0] - int(pad * SR)), min(len(x), idx[-1] + int(pad * SR))
    return x[a:b]


def tempo(x: np.ndarray, factor: float, work: Path) -> np.ndarray:
    if abs(factor - 1) < 0.02:
        return x
    a, b = work / "t_in.wav", work / "t_out.wav"
    sf.write(a, x, SR)
    media.run(["-i", a, "-filter:a", f"atempo={factor:.4f}", b])
    y, _ = sf.read(b, dtype="float32")
    return y


async def run_dub_render(job):
    p = dict(job.params)
    proj = load_project(p["project"])
    # apply edits from the review screen
    for k in ("segments", "speakers", "engine", "quality", "subtitles", "lipsync", "subtitle_style"):
        if p.get(k) is not None:
            proj[k] = p[k]
    save_project(proj)
    preview = bool(p.get("preview"))
    limit = float(p.get("preview_seconds", 30)) if preview else 1e9
    work = DUB / proj["id"] / ("preview" if preview else "render")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    q = QUALITY.get(proj["quality"], QUALITY["balanced"])
    engine = proj["engine"]
    tgt, src_lang = proj["target_language"], proj["source_language"]
    info = proj["info"]
    note = lambda m: setattr(job, "message", m)

    await resources.prepare(engine, f"voice:{engine}", notify=note)
    await resources.unload_family("comfyui")
    await resources.unload_family("media")

    from jobs import presets
    params = dict(presets()["voice"].get(q["preset"], {}).get(engine, {}))
    if engine == "chatterbox" and src_lang != tgt:
        params["cfg_weight"] = 0.2   # official tip: low CFG avoids carrying the source accent across languages
    spk = {s["id"]: s for s in proj["speakers"]}
    segs = [s for s in proj["segments"] if s["start"] < limit and s.get("translation", "").strip()]
    clips = []
    t0 = time.time()
    for n, s in enumerate(segs):
        if job.cancel_requested:
            raise RuntimeError("Generation was cancelled.")
        job.message = f"Generating voice {n + 1}/{len(segs)}…"
        job.progress = 0.05 + 0.65 * n / max(1, len(segs))
        sp = spk.get(s["speaker"], {})
        if sp.get("enabled") is False:     # keep this speaker's original voice (from the separated vocals)
            out = work / f"seg_{n:04d}.wav"
            cut_audio(proj.get("vocals") or proj["audio"], s["start"], s["end"], out, sr=SR)
            clips.append((s, load_mono(out)))
            continue
        ref, ref_text = sp.get("ref"), sp.get("ref_text")
        if sp.get("voice") == "saved" and sp.get("voice_id"):
            v = db.get_voice(int(sp["voice_id"]))
            if v:
                ref, ref_text = str(ROOT / v["ref_path"]), v.get("transcript")
        elif sp.get("voice") == "custom" and sp.get("custom_ref"):
            ref, ref_text = str(resolve_input(sp["custom_ref"])), None
        out = work / f"seg_{n:04d}.wav"
        await tts(engine, s["translation"], tgt, ref, ref_text, out, params)
        clips.append((s, trim_silence(load_mono(out))))

    job.message, job.progress = "Synchronizing timing…", 0.75
    placed, shift_total = [], 0.0
    cursor = 0.0
    for k, (s, x) in enumerate(clips):
        slot_end = clips[k + 1][0]["start"] if k + 1 < len(clips) else s["end"] + 1.0
        avail = max(0.3, slot_end - s["start"] - 0.08)
        dur = len(x) / SR
        f = 1.0
        if dur > avail:
            f = min(q["max_tempo"], dur / avail)
            x = tempo(x, f, work)
        start = max(s["start"], cursor + 0.05)       # never overlap: carry the delay forward
        placed.append((start, x, f))
        cursor = start + len(x) / SR
        shift_total = max(shift_total, cursor - slot_end)
    duration = min(info["duration"], limit) if preview else info["duration"]
    total = max(duration, cursor + 0.3)
    extension = max(0.0, total - duration)

    job.message, job.progress = "Mixing audio…", 0.82
    voice = np.zeros(int(total * SR) + SR, dtype=np.float32)
    for start, x, _ in placed:
        a = int(start * SR)
        voice[a:a + len(x)] += x[: len(voice) - a]
    peak = np.max(np.abs(voice)) or 1
    voice *= min(1.0, 0.89 / peak)
    if proj.get("background"):
        bg, bsr = sf.read(proj["background"], dtype="float32", always_2d=True)
        if bsr != SR:
            tmp = work / "bg48.wav"
            media.run(["-i", proj["background"], "-ar", SR, tmp])
            bg, _ = sf.read(tmp, dtype="float32", always_2d=True)
        bg = bg[: int(total * SR)]
        mix = np.zeros((len(voice), 2), dtype=np.float32)
        mix[: len(bg)] += bg
        mix += voice[:, None]
    else:
        mix = np.repeat(voice[:, None], 2, axis=1)
    mix = np.tanh(mix * 0.98)          # soft limiter
    mix_path = work / "mix.wav"
    sf.write(mix_path, mix[: int(total * SR)], SR)
    voice_path = work / "voice_only.wav"
    sf.write(voice_path, voice[: int(total * SR)], SR)

    video_in = proj["video"]
    lip = p.get("lipsync") or proj.get("lipsync") or "off"
    if lip != "off":
        import avatar
        job.message, job.progress = "Lip sync…", 0.84
        src_for_lip = video_in
        if preview:     # only the preview range
            src_for_lip = work / "src_preview.mp4"
            media.run(["-i", video_in, "-t", f"{min(duration, total):.3f}", "-an", "-c:v", "libx264", "-crf", "12",
                       "-pix_fmt", "yuv420p", src_for_lip])
        video_in = str(await avatar.lipsync_video(job, Path(src_for_lip), mix_path, voice_path, work / "lip",
                                                  "quality" if lip == "quality" else "balanced", progress=(0.84, 0.97)))

    job.message, job.progress = "Rendering…", 0.9
    vf = []
    if extension > 0.05:
        vf.append(f"tpad=stop_mode=clone:stop_duration={extension:.3f}")
    sub_mode = proj.get("subtitles", "off")
    srt_out = None
    if sub_mode != "off":
        placed_segs = [{"start": st, "end": st + len(x) / SR, "text": s["text"], "translation": s["translation"]}
                       for (st, x, _), (s, _) in zip(placed, clips)]
        ass = work / "subs.ass"
        ass.write_text(captions.subtitle_track(placed_segs, info["width"], info["height"], sub_mode, proj.get("subtitle_style", "minimal")), encoding="utf-8")
        vf.append(f"subtitles='{captions.ffmpeg_path(ass)}'")
        srt_out = captions.srt(placed_segs, "translation" if sub_mode != "original" else "text")
    tag = f"dub{tgt}" + ("preview" if preview else "")
    folder = OUT_TRANSLATED if lip != "off" else OUT_DUBBING
    folder.mkdir(parents=True, exist_ok=True)
    dst = next_output_path(folder, tag, "mp4")
    args = []
    if preview:
        args += ["-t", f"{total:.3f}"]
    args += ["-i", video_in, "-i", mix_path, "-map", "0:v:0", "-map", "1:a:0"]
    if vf:
        args += ["-vf", ",".join(vf), "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
    elif preview or video_in != proj["video"]:
        args += ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", "copy"]
    args += ["-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", dst]
    media.run(args)
    if srt_out:
        dst.with_suffix(".srt").write_text(srt_out, encoding="utf-8")
    secs = round(time.time() - t0, 1)
    speedups = [f for _, _, f in placed if f > 1.01]
    job.gen_id = db.add_generation(
        type="video", engine=engine, model=f"Dub {src_lang}→{tgt}", prompt=" ".join(s["translation"] for s, _ in clips)[:400],
        params={"project": proj["id"], "engine": engine, "quality": proj["quality"], "subtitles": sub_mode,
                "preview": preview, "speakers": len(proj["speakers"])},
        inputs={"video": proj["video_rel"]}, outputs=[rel(dst)], status="done", seconds=secs)
    job.result = {"outputs": [rel(dst)], "seconds": secs, "project": proj["id"], "extension": round(extension, 2),
                  "speedups": len(speedups), "max_speedup": round(max(speedups, default=1.0), 2)}
    await resources.after_job(engine)
