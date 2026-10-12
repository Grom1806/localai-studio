"""AI clip creator (OpusClip-style): find highlights in a long video, reframe, caption, export.

analyze:  transcribe (faster-whisper) → sentence/duplicate checks → LLM highlight scoring + sampled-frame review
          → transcript-grounded titles, descriptions, tags and platform captions
export:   cut (trim / deleted sections / silence & filler removal) → face-tracked smooth reframing →
          word-synced captions + hook title → music / fades / B-roll → H.264 MP4
"""
import json
import logging
import math
import re
import shutil
import time
import uuid
from difflib import SequenceMatcher
from pathlib import Path

import captions
import db
import llm
import media
import resources
from dubbing import FILLERS, build_utterances, media_call
from jobs import next_output_path, rel, resolve_input
from settings import DATA, OUT_CLIPS, OUT_VIDEOS, ROOT

log = logging.getLogger("clips")
CLIPS = DATA / "clips"
CLIPS.mkdir(parents=True, exist_ok=True)
LENGTHS = {"short": (15, 30), "medium": (30, 60), "long": (60, 90)}
EXPORT = {"tiktok": (1080, 1920), "reels": (1080, 1920), "shorts": (1080, 1920), "youtube": (1920, 1080),
          "square": (1080, 1080), "portrait45": (1080, 1350)}
ASPECTS = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0, "4:5": 4 / 5}
WEIGHTS = {"hook": 0.30, "interest": 0.20, "context": 0.20, "engagement": 0.15, "shareability": 0.15}


def ppath(pid):
    return CLIPS / pid / "project.json"


def load(pid):
    return json.loads(ppath(pid).read_text(encoding="utf-8"))


def save(p):
    ppath(p["id"]).write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def prior_clips_for_source(src: Path) -> list[dict]:
    """Return prior clip ranges for this exact local source video."""
    target = Path(src).resolve()
    prior = []
    for project_path in CLIPS.glob("*/project.json"):
        try:
            project = json.loads(project_path.read_text(encoding="utf-8"))
            if not project.get("video") or Path(project["video"]).resolve() != target:
                continue
            for clip in project.get("clips", []):
                prior.append({"start": float(clip["start"]), "end": float(clip["end"]),
                              "text": str(clip.get("text") or "")})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return prior


def fmt(t):
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def transcript_language(text: str, detected: str | None) -> str:
    """Prefer the language actually present in the transcript when audio detection is mixed."""
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", text or ""))
    arabic = len(re.findall(r"[\u0600-\u06FF]", text or ""))
    if cyrillic >= 40 and cyrillic >= arabic * 1.4:
        return "ru"
    if arabic >= 40 and arabic >= cyrillic * 1.4:
        return "ar"
    return detected or "ru"


def sample_video_frames(src, start: float, end: float, folder: Path, count: int = 4,
                        extra_times: list[float] | None = None, max_total: int | None = None) -> list[dict]:
    """Save small, evenly spaced stills for local multimodal review; never uploads the source video."""
    folder.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, float(end) - float(start))
    count = max(1, int(count))
    fractions = [i / max(1, count - 1) for i in range(count)]
    if count > 1:
        fractions[0], fractions[-1] = 0.02, 0.98
    uniform_times = [float(start) + duration * fraction for fraction in fractions]
    scene_times = []
    for at in extra_times or []:
        try:
            at = float(at)
        except (TypeError, ValueError):
            continue
        if start <= at < end:
            at = min(float(end) - 0.02, max(float(start), at))
            if all(abs(at - other) >= 0.65 for other in scene_times):
                scene_times.append(at)
    limit = max(1, int(max_total)) if max_total is not None else count + min(3, len(scene_times))
    chosen = scene_times[:limit]
    remaining_uniform = [at for at in uniform_times if all(abs(at - scene) >= 0.65 for scene in chosen)]
    slots = max(0, limit - len(chosen))
    if len(remaining_uniform) > slots:
        if slots == 0:
            remaining_uniform = []
        elif slots == 1:
            remaining_uniform = [remaining_uniform[len(remaining_uniform) // 2]]
        else:
            indices = [round(i * (len(remaining_uniform) - 1) / (slots - 1)) for i in range(slots)]
            remaining_uniform = [remaining_uniform[index] for index in dict.fromkeys(indices)]
    unique_times = sorted(chosen + remaining_uniform)
    bounded_times = []
    for at in unique_times:
        at = min(float(end) - 0.02, max(float(start), at))
        if not bounded_times or at - bounded_times[-1] >= 0.65:
            bounded_times.append(at)
    frames = []
    for i, at in enumerate(bounded_times):
        dst = folder / f"frame_{i + 1:02d}.jpg"
        try:
            media.run(["-ss", f"{max(0, at):.3f}", "-i", src, "-frames:v", "1",
                       "-vf", "scale=512:512:force_original_aspect_ratio=decrease", "-q:v", "5", dst], timeout=120)
            if dst.exists() and dst.stat().st_size > 512:
                frames.append({"path": str(dst), "rel": rel(dst), "time": round(at, 2)})
        except Exception as e:
            log.warning("Could not sample video frame at %.2fs: %s", at, e)
    return frames


def detect_scene_changes(src, duration: float, threshold: float = 0.32,
                        max_changes: int = 24) -> tuple[list[dict], bool]:
    """Detect likely shot cuts with a low-rate FFmpeg scene score; these are cues, not editorial decisions."""
    if duration < 2:
        return [], False
    try:
        result = media.run(["-i", src, "-loglevel", "info", "-vf",
                            f"fps=2,select='gt(scene,{threshold:.2f})',metadata=print",
                            "-an", "-f", "null", "-"], timeout=max(120, min(7200, int(duration * 2 + 60))))
    except Exception as exc:
        log.warning("Scene-change detection failed for %s: %s", src, exc)
        return [], False
    candidates = []
    current_time = None
    for line in (result.stderr or "").splitlines():
        time_match = re.search(r"frame:\s*\d+.*?pts_time:([0-9.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
        score_match = re.search(r"lavfi\.scene_score=([0-9.]+)", line)
        if score_match and current_time is not None:
            score = float(score_match.group(1))
            if score >= threshold and 0.6 < current_time < duration - 0.6:
                candidates.append({"time": round(current_time, 3), "score": round(score, 3)})
    # Avoid reporting a burst of near-identical detections during a flash/fade as many cuts.
    selected = []
    for item in sorted(candidates, key=lambda value: value["score"], reverse=True):
        if all(abs(item["time"] - other["time"]) >= 1.25 for other in selected):
            selected.append(item)
            if len(selected) >= max_changes:
                break
    return sorted(selected, key=lambda value: value["time"]), True


def scene_frame_times(scene_changes: list[dict], duration: float, limit: int = 3) -> list[float]:
    """Pick one stable post-cut frame from each temporal third, favoring stronger cut evidence."""
    buckets: dict[int, dict] = {}
    for change in scene_changes:
        bucket = min(limit - 1, int(float(change["time"]) / max(duration, 1) * limit))
        if bucket not in buckets or float(change["score"]) > float(buckets[bucket]["score"]):
            buckets[bucket] = change
    return [min(duration - 0.15, float(buckets[index]["time"]) + 0.35)
            for index in sorted(buckets) if float(buckets[index]["time"]) + 0.35 < duration - 0.05][:limit]


def audio_level(src, start: float, duration: float) -> dict:
    """Return objective FFmpeg volume measurements; this is not a subjective sound-quality rating."""
    try:
        result = media.run(["-loglevel", "info", "-ss", f"{max(0, start):.3f}", "-t", f"{min(max(duration, 0.1), 20):.2f}",
                            "-i", src, "-vn", "-af", "volumedetect", "-f", "null", "-"], timeout=90)
        logtext = result.stderr or ""
        def val(name):
            m = re.search(rf"{name}:\s*(-?\d+(?:\.\d+)?)\s*dB", logtext)
            return round(float(m.group(1)), 1) if m else None
        return {"mean_db": val("mean_volume"), "peak_db": val("max_volume")}
    except Exception as e:
        log.info("Audio level analysis unavailable: %s", e)
        return {"mean_db": None, "peak_db": None}


# ─────────────────────────── highlight detection ───────────────────────────
def heuristic_candidates(sents, min_s, max_s):
    """Fallback without an LLM: questions, exclamations, lively speech rate and self-contained length."""
    out = []
    for i in range(len(sents)):
        j, dur = i, 0
        while j < len(sents) and sents[j]["end"] - sents[i]["start"] < min_s:
            j += 1
        if j >= len(sents) or sents[j]["end"] - sents[i]["start"] > max_s:
            continue
        text = " ".join(s["text"] for s in sents[i:j + 1])
        words = len(text.split())
        rate = words / max(1, sents[j]["end"] - sents[i]["start"])
        hook = 55 + 20 * ("?" in sents[i]["text"]) + 10 * ("!" in sents[i]["text"])
        eng = min(95, 40 + rate * 12)
        out.append({"start": i, "end": j, "hook": hook, "interest": 60, "context": 60 if i else 70,
                    "engagement": int(eng), "shareability": 55, "reason": "heuristic (no local LLM)"})
    return out


def overall(c):
    return round(sum(c[k] * w for k, w in WEIGHTS.items()))


def near_duplicate_text(a: str, b: str) -> bool:
    """Catch repeated passages even when they occur at different times in the source video."""
    norm = lambda s: " ".join(re.findall(r"\w+", (s or "").casefold(), flags=re.UNICODE))
    a, b = norm(a), norm(b)
    return len(a) >= 60 and len(b) >= 60 and SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.78


def snap(sents, a, b, min_s, max_s):
    """Adjust a sentence range so the clip length fits [min_s, max_s] without cutting a sentence."""
    a, b = max(0, min(a, len(sents) - 1)), max(0, min(b, len(sents) - 1))
    if b < a:
        a, b = b, a
    while sents[b]["end"] - sents[a]["start"] < min_s and b + 1 < len(sents):
        b += 1
    while sents[b]["end"] - sents[a]["start"] > max_s and b > a:
        b -= 1
    return a, b


async def run_clips_analyze(job):
    p = dict(job.params)
    src = resolve_input(p["video"])
    info = media.probe(src)
    min_s, max_s = (float(p["min_seconds"]), float(p["max_seconds"])) if p.get("length") == "custom" else \
        LENGTHS.get(p.get("length", "medium"), LENGTHS["medium"])
    want = int(p.get("count", 5))
    prior = prior_clips_for_source(src) if p.get("exclude_existing") else []
    candidate_want = min(30, max(12, want * 2)) if prior else max(3, want)
    pid = uuid.uuid4().hex[:10]
    work = CLIPS / pid
    work.mkdir(parents=True)
    note = lambda m: setattr(job, "message", m)

    await resources.prepare("media", "media:clips", notify=note)
    await resources.unload_family("comfyui")
    job.message, job.progress = "Extracting audio…", 0.03
    audio = work / "audio.wav"
    media.run(["-i", src, "-vn", "-ac", "1", "-ar", 16000, audio])
    job.message, job.progress = "Transcribing the whole video…", 0.1
    lang = p.get("language") if p.get("language") not in (None, "", "auto") else None
    tr = await media_call("/transcribe", {"path": str(audio), "language": lang}, timeout=7200)
    lang = tr["language"]
    sents = build_utterances(tr["segments"], None, max_len=20.0, gap=1.2)
    for k, s in enumerate(sents):
        s["i"] = k
    if not sents:
        raise ValueError("No speech found in this video.")
    lang = transcript_language(" ".join(s["text"] for s in sents), lang)
    await resources.unload_family("media")

    cands, scorer = [], "llm"
    try:
        await llm.ensure(note)
        # overlapping windows of ~7 minutes so the model sees enough context each time
        win, step = 420.0, 300.0
        t, total = 0.0, sents[-1]["end"]
        nwin = max(1, math.ceil(max(0, total - win) / step) + 1)
        k = 0
        while True:
            chunk = [s for s in sents if s["start"] >= t and s["end"] <= t + win]
            if chunk:
                k += 1
                job.message = f"Finding highlights ({k}/{nwin})…"
                job.progress = 0.35 + 0.45 * k / nwin
                cands += await llm.find_highlights(chunk, min_s, max_s, candidate_want, lang)
            if t + win >= total:
                break
            t += step
    except Exception as e:
        log.warning("LLM highlight scoring unavailable, using heuristics: %s", e)
        scorer = "heuristic"
        cands = heuristic_candidates(sents, min_s, max_s)

    # validate, snap to sentence boundaries, score, de-duplicate overlapping picks
    ranked = []
    for c in cands:
        try:
            a, b = snap(sents, int(c["start"]), int(c["end"]), min_s, max_s)
        except Exception:
            continue
        dur = sents[b]["end"] - sents[a]["start"]
        if dur < min_s * 0.8 or dur > max_s * 1.1:
            continue
        c = {k: max(0, min(100, int(c.get(k, 50)))) for k in WEIGHTS} | {"reason": c.get("reason", ""), "a": a, "b": b}
        c["score"] = overall(c)
        ranked.append(c)
    ranked.sort(key=lambda c: -c["score"])
    chosen = []
    for c in ranked:
        s0, e0 = sents[c["a"]]["start"], sents[c["b"]]["end"]
        if any(min(e0, x["end"]) - max(s0, x["start"]) > 0.3 * (e0 - s0) for x in chosen):
            continue
        text = " ".join(s["text"] for s in sents[c["a"]:c["b"] + 1])
        if any(near_duplicate_text(text, x["text"]) for x in chosen):
            continue
        if any(min(e0, x["end"]) - max(s0, x["start"]) > 0.3 * min(e0 - s0, x["end"] - x["start"])
               or near_duplicate_text(text, x["text"]) for x in prior):
            continue
        words = [dict(w, start=round(w["start"] - s0, 3), end=round(w["end"] - s0, 3))
                 for s in sents[c["a"]:c["b"] + 1] for w in s["words"]]
        chosen.append({"id": f"c{len(chosen) + 1}", "start": s0, "end": e0, "score": c["score"],
                       **{k: c[k] for k in WEIGHTS}, "reason": c["reason"],
                       "text": text, "words": words,
                       "titles": [], "status": "candidate", "label": f"{fmt(s0)} → {fmt(e0)}"})
        if len(chosen) >= want:
            break

    # Social metadata and clip-level visual/audio review are intentionally deferred until
    # the user asks to preview a specific clip for YouTube (or explicitly analyzes it in Edit).
    job.message, job.progress = "Preparing vertical clip previews…", 0.85
    if scorer == "llm":
        await resources.unload_family("llm")

    proj = {"id": pid, "created": time.time(), "video": str(src), "video_rel": rel(src), "info": info,
            "language": lang, "scorer": scorer, "length": [min_s, max_s], "clips": chosen,
            "sentences": [{k: s[k] for k in ("start", "end", "text")} for s in sents]}
    save(proj)
    # vertical 9:16 previews for the review cards (fast, half resolution, tracked + captioned)
    job.progress = 0.97
    await make_previews(proj, chosen, note=lambda m: setattr(job, "message", m))
    save(proj)
    job.result = {"project": pid, "clips": len(chosen), "scorer": scorer, "language": lang,
                  "existing_clips_avoided": len(prior), "outputs": []}


async def run_clips_enrich(job):
    """Re-review an edited clip's current range and regenerate factual platform metadata."""
    p = job.params
    proj = load(p["project"])
    clip = next((c for c in proj["clips"] if c["id"] == p["clip"]), None)
    if not clip:
        raise ValueError("Clip not found in this project.")
    src = resolve_input(proj.get("video") or proj.get("video_rel"))
    start = float(p.get("start", clip["start"]))
    end = float(p.get("end", clip["end"]))
    if start < 0 or end <= start:
        raise ValueError("Choose a valid start and end time for this clip.")
    folder = CLIPS / proj["id"] / clip["id"] / "analysis_frames"
    frames = sample_video_frames(src, start, end, folder, count=4)
    metrics = audio_level(src, start, end - start) if proj.get("info", {}).get("has_audio") else None
    note = lambda m: setattr(job, "message", m)
    await llm.ensure(note)
    try:
        job.message, job.progress = "Reviewing sampled frames and preparing titles, descriptions and tags…", 0.35
        utterances = [s.get("text", "") for s in proj.get("sentences", [])
                      if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end]
        transcript = " ".join(x for x in utterances if x) or clip.get("text", "")
        lang = transcript_language(transcript, proj.get("language", "ru"))
        visual_review = bool(frames)
        try:
            analysis = await llm.analyze_content(transcript, frames, lang)
        except Exception as e:
            log.warning("Visual re-review failed for %s; generating transcript-only metadata: %s", clip["id"], e)
            analysis = await llm.analyze_content(transcript, [], lang)
            visual_review = False
        analysis["visual_review_available"] = visual_review
        analysis["audio_metrics"] = metrics
        analysis["reviewed_range"] = [start, end]
        try:
            clip["titles"] = await llm.hook_titles(transcript, lang)
        except Exception as title_error:
            log.warning("Could not generate on-screen titles for %s: %s", clip["id"], title_error)
            clip["titles"] = clip.get("titles", [])
        clip["visual_frames"] = [{"path": f["rel"], "time": f["time"]} for f in frames]
        clip["audio_metrics"] = metrics
        clip["analysis"] = analysis
        clip["social"] = {k: analysis.get(k, {}) for k in ("youtube", "instagram", "tiktok")}
        proj["language"] = lang
        save(proj)
        job.result = {"outputs": [], "clip": clip}
    finally:
        await resources.after_job("llm")


async def run_video_analyze(job):
    """Analyze a whole video locally: transcribe speech, sample frames, review and draft social metadata."""
    p = job.params
    src = resolve_input(p["video"])
    info = media.probe(src)
    if not info.get("has_video"):
        raise ValueError("This file does not contain a video track.")
    aid = uuid.uuid4().hex[:10]
    work = CLIPS / "video_analysis" / aid
    work.mkdir(parents=True, exist_ok=True)
    note = lambda m: setattr(job, "message", m)
    transcript, lang, words = "", "ru", []
    if info.get("has_audio"):
        await resources.prepare("media", "media:clips", notify=note)
        await resources.unload_family("comfyui")
        job.message, job.progress = "Preparing audio and transcribing the video…", 0.08
        audio = work / "audio.wav"
        media.run(["-i", src, "-vn", "-ac", "1", "-ar", "16000", audio], timeout=7200)
        tr = await media_call("/transcribe", {"path": str(audio), "language": p.get("language")}, timeout=7200)
        lang = tr.get("language") or lang
        transcript = " ".join(seg.get("text", "").strip() for seg in tr.get("segments", []) if seg.get("text"))
        words = [
            {"start": float(word["start"]), "end": float(word["end"]), "word": str(word["word"]).strip()}
            for segment in tr.get("segments", []) for word in segment.get("words", [])
            if isinstance(word, dict) and word.get("word") and word.get("start") is not None and word.get("end") is not None and
            float(word.get("end", 0) or 0) > float(word.get("start", 0) or 0)
        ]
        await resources.unload_family("media")
    edit_only = p.get("purpose") == "editor"
    scene_changes = []
    if edit_only:
        job.message, job.progress = "Finding likely shot changes for the editor…", 0.46
        scene_changes, scene_detection_available = detect_scene_changes(src, info["duration"])
        frames = sample_video_frames(src, 0, info["duration"], work / "frames", count=8,
                                     extra_times=scene_frame_times(scene_changes, info["duration"]), max_total=8)
    else:
        frames = sample_video_frames(src, 0, info["duration"], work / "frames", count=8)
    visual_balance = media.analyze_frame_levels([frame["path"] for frame in frames]) if edit_only else None
    job.message, job.progress = ("Analyzing the video for the editing timeline…" if edit_only else
                                 "Analyzing the video frames and preparing platform metadata…"), 0.55
    await llm.ensure(note)
    try:
        analysis = (await llm.analyze_for_editing(transcript, frames, lang, scene_changes=scene_changes,
                                                  scene_detection_available=scene_detection_available) if edit_only else
                    await llm.analyze_content(transcript, frames, lang, full_video=True))
        analysis["visual_review_available"] = bool(frames)
        analysis["audio_metrics"] = audio_level(src, 0, info["duration"]) if info.get("has_audio") else None
        analysis["frames"] = [{"path": f["rel"], "time": f["time"]} for f in frames]
        if edit_only:
            analysis["scene_changes"] = scene_changes
            analysis["scene_detection_available"] = scene_detection_available
            analysis["visual_balance"] = visual_balance
        analysis["duration"] = info["duration"]
        analysis["source"] = rel(src)
        job.result = {"outputs": [], "analysis": analysis,
                      "transcription": {"language": lang, "words": words}}
    finally:
        await resources.after_job("llm")


# ─────────────────────────── export ───────────────────────────
def keep_intervals(clip, words, cuts, silence_mode):
    """Intervals (relative to clip start) that survive trims, deleted sections and silence/filler removal."""
    dur = clip["end"] - clip["start"]
    keep = [(0.0, dur)]

    def remove(a, b):
        nonlocal keep
        out = []
        for x, y in keep:
            if b <= x or a >= y:
                out.append((x, y))
                continue
            if a > x:
                out.append((x, a))
            if b < y:
                out.append((b, y))
        keep = out

    for a, b in cuts or []:
        remove(float(a), float(b))
    if silence_mode in ("light", "aggressive") and words:
        min_gap, leave = (1.2, 0.35) if silence_mode == "light" else (0.55, 0.18)
        for w0, w1 in zip(words, words[1:]):
            if w1["start"] - w0["end"] > min_gap:
                remove(w0["end"] + leave / 2, w1["start"] - leave / 2)
        if silence_mode == "aggressive":
            for w in words:
                if w["word"].strip().lower().strip(".,!?…") in FILLERS:
                    remove(w["start"], w["end"])
    return [(a, b) for a, b in keep if b - a > 0.08]


def remap_words(words, keep):
    """Map clip-relative word times onto the cut timeline; drop words that were removed."""
    out, offset = [], 0.0
    for a, b in keep:
        for w in words:
            if w["start"] >= a - 0.02 and w["end"] <= b + 0.05:
                out.append({**w, "start": round(w["start"] - a + offset, 3),
                            "end": round(min(w["end"], b) - a + offset, 3)})
        offset += b - a
    return out


def crop_path(track, aspect, zoom, out_fps, duration, split=False):
    """Smoothed per-frame crop centre (source pixels) that follows the active speaker."""
    W, H = track["width"], track["height"]
    ar = ASPECTS[aspect]
    ch = H / zoom
    cw = min(W, ch * ar)
    if cw >= W:          # target is wider than the source: crop height instead
        cw = W / zoom
        ch = min(H, cw / ar)
    samples = track["samples"]
    # active speaker: the face whose mouth moves most over the surrounding ~1.5 s
    centres = []
    for k, s in enumerate(samples):
        if not s["faces"]:
            centres.append(None)
            continue
        win = samples[max(0, k - 4):k + 5]

        def activity(f):
            vals = [g["mouth"] for w in win for g in w["faces"] if abs(g["x"] - f["x"]) < 0.08]
            return (max(vals) - min(vals) if len(vals) > 1 else 0) + f["w"] * 0.05
        best = max(s["faces"], key=activity)
        centres.append((best["x"] * W, best["y"] * H))
    # fill gaps
    last = (W / 2, H / 2)
    for k in range(len(centres)):
        if centres[k] is None:
            centres[k] = last
        last = centres[k]
    # smoothing: dead-zone + critically-damped follow, capped speed (no jitter, no whip pans)
    fx, fy = centres[0] if centres else (W / 2, H / 2)
    dead, maxv = 0.06 * cw, 0.35 * cw     # px, px per second
    times = [s["t"] - track.get("t0", 0) for s in samples] or [0.0]
    out, j = [], 0
    n = int(duration * out_fps) + 1
    for f in range(n):
        t = f / out_fps
        while j + 1 < len(times) and times[j + 1] <= t:
            j += 1
        tx, ty = centres[j] if centres else (W / 2, H / 2)
        dx = tx - fx
        if abs(dx) > dead:
            fx += max(-maxv / out_fps, min(maxv / out_fps, (dx - math.copysign(dead, dx)) * 0.08))
        fy += (ty - fy) * 0.03
        x = min(max(0, fx - cw / 2), W - cw)
        y = min(max(0, fy - ch * 0.42), H - ch)       # keep eyes in the upper part of the frame
        out.append((t, int(x), int(y)))
    return int(cw) // 2 * 2, int(ch) // 2 * 2, out


def out_size(edits: dict, preview: bool) -> tuple[int, int]:
    aspect = edits.get("aspect", "9:16")
    preset = edits.get("export", "tiktok" if aspect == "9:16" else "youtube")
    w, h = EXPORT.get(preset, (1080, 1920))
    if aspect == "1:1":
        w, h = 1080, 1080
    elif aspect == "4:5":
        w, h = 1080, 1350
    elif aspect == "9:16":
        w, h = 1080, 1920
    elif aspect == "16:9":
        w, h = 1920, 1080
    return (w // 2 // 2 * 2, h // 2 // 2 * 2) if preview else (w, h)


async def render_clip(proj: dict, clip: dict, edits: dict, dst: Path, work: Path, preview=False, note=None) -> float:
    """Cut -> track/reframe -> captions/title -> B-roll/music -> H.264. preview=True: half size, fast encode."""
    note = note or (lambda m: None)
    info = proj["info"]
    aspect = edits.get("aspect", "9:16")
    out_w, out_h = out_size(edits, preview)
    fps = 30 if preview else int(edits.get("fps", 30))
    fade = float(edits.get("fade", 0.3))
    work.mkdir(parents=True, exist_ok=True)

    # 1) cut (trim / deleted sections / silence + filler removal)
    note("Cutting…")
    words = clip["words"]
    keep = keep_intervals(clip, words, clip.get("cuts"), edits.get("silence", "light"))
    # burned-in black bars (letterboxed sources) are cropped away so they do not end up inside the 9:16 frame
    bars = media.detect_bars(proj["video"], clip["start"], clip["end"] - clip["start"], info["width"], info["height"]) \
        if edits.get("remove_bars", True) else None
    bar_crop = f",crop={bars[0]}:{bars[1]}:{bars[2]}:{bars[3]}" if bars else ""
    parts, fc = [], []
    for k, (a, b) in enumerate(keep):
        s0, s1 = clip["start"] + a, clip["start"] + b
        fc.append(f"[0:v]trim={s0:.3f}:{s1:.3f},setpts=PTS-STARTPTS{bar_crop}[v{k}];"
                  f"[0:a]atrim={s0:.3f}:{s1:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.015,"
                  f"afade=t=out:st={max(0, b - a - 0.015):.3f}:d=0.015[a{k}]")
        parts.append(f"[v{k}][a{k}]")
    fc.append("".join(parts) + f"concat=n={len(keep)}:v=1:a=1[v][a]")
    cut = work / "cut.mov"
    media.run(["-i", proj["video"], "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-crf", "23" if preview else "14", "-preset", "ultrafast" if preview else "veryfast",
               "-r", fps, "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", cut])
    cut_dur = sum(b - a for a, b in keep)
    words = remap_words(words, keep)

    # 2) reframe: faces -> smooth active-speaker crop; no faces (scenery, slides) -> fit + blurred background
    src_ar = bars[0] / bars[1] if bars else info["width"] / info["height"]
    vf, pre_chain = [], None
    cap_margin, title_margin = 0.14, 0.07
    clip["render_warnings"] = []
    layout = edits.get("layout", "auto")
    if abs(ASPECTS[aspect] - src_ar) > 0.05 or float(edits.get("zoom", 1.0)) > 1.01:
        face_ratio, track = 1.0, None
        if layout != "fit":
            note("Tracking faces…")
            try:
                await resources.prepare("media", "media:track", notify=note)
                track = await media_call("/track", {"path": str(cut), "start": 0.0, "end": cut_dur,
                                                    "sample_fps": 4 if preview else 6})
                face_ratio = sum(1 for s in track["samples"] if s["faces"]) / max(1, len(track["samples"]))
            except Exception as exc:
                warning = "Отслеживание лица недоступно: использован полный кадр с размытым портретным фоном."
                log.warning("Face tracking unavailable for clip %s; using fit layout: %s", clip.get("id"), exc)
                clip["render_warnings"].append(warning)
                note("Face tracking unavailable; preserving the full shot over a blurred background…")
                layout, face_ratio = "fit", 0.0
        if layout == "fit" or (layout == "auto" and face_ratio < 0.3 and ASPECTS[aspect] < src_ar):
            fg_h = int(out_w / src_ar) // 2 * 2
            band = (out_h - fg_h) / 2 / out_h                  # height of each blurred band (relative)
            cap_margin, title_margin = min(0.45, band + 0.04), min(0.45, band + 0.03)
            pre_chain = (f"[0:v]split[pa][pb];[pa]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                         f"crop={out_w}:{out_h},boxblur=24:2,eq=brightness=-0.10[pbg];"
                         f"[pb]scale={out_w}:{fg_h},setsar=1[pfg];[pbg][pfg]overlay=0:(H-h)/2")
        else:
            cw, ch, path = crop_path(track, aspect, float(edits.get("zoom", 1.0)), fps, cut_dur)
            cmds = work / "crop.cmd"
            cmds.write_text("".join(f"{t:.4f} crop@c x {x}, crop@c y {y};\n" for t, x, y in path), encoding="utf-8")
            vf.append(f"sendcmd=f='{captions.ffmpeg_path(cmds)}',crop@c=w={cw}:h={ch}:x={path[0][1]}:y={path[0][2]}")
    if pre_chain is None:
        vf.append(f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},setsar=1")

    # 3) word-synced captions + hook title
    note("Adding captions…")
    if not proj.get("headline", True) and "title" not in edits:
        title = None                                   # "Auto headline" switched off for this project
    else:
        title = edits.get("title") if "title" in edits else (clip.get("title") or (clip.get("titles") or [None])[0])
    if edits.get("captions", True) and words:
        ass = work / "captions.ass"
        ass.write_text(captions.word_captions(
            words, out_w, out_h, edits.get("style", "opus"), edits.get("position", "bottom"),
            int(edits.get("words_per_line", 3)), bool(edits.get("highlight", True)), edits.get("box"),
            edits.get("outline"), edits.get("font_size"), title, float(edits.get("title_seconds", 5.0)), cut_dur,
            margin_v=cap_margin, title_margin=title_margin), encoding="utf-8")
        vf.append(f"subtitles='{captions.ffmpeg_path(ass)}'")
    elif title:
        ass = work / "title.ass"
        ass.write_text(captions.word_captions([], out_w, out_h, edits.get("style", "opus"), title=title,
                                              title_end=float(edits.get("title_seconds", 5.0)),
                                              title_margin=title_margin), encoding="utf-8")
        vf.append(f"subtitles='{captions.ffmpeg_path(ass)}'")
    if fade > 0:
        vf.append(f"fade=t=in:d={fade},fade=t=out:st={max(0, cut_dur - fade):.3f}:d={fade}")

    # 4) B-roll cut-aways (local files only), music, volume
    inputs = ["-i", cut]
    fc = [(f"{pre_chain}," if pre_chain else "[0:v]") + ",".join(vf) + "[base]"]
    vlabel = "base"
    for k, br in enumerate(edits.get("broll") or []):
        bpath = resolve_input(br["path"])
        inputs += ["-i", bpath] if bpath.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv") else \
            ["-loop", "1", "-t", f"{cut_dur:.3f}", "-i", bpath]
        a, b = float(br["start"]), float(br["end"])
        fc.append(f"[{k + 1}:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},"
                  f"setpts=PTS-STARTPTS+{a}/TB[b{k}];[{vlabel}][b{k}]overlay=enable='between(t,{a},{b})'[o{k}]")
        vlabel = f"o{k}"
    vol = float(edits.get("volume", 1.0))
    fc.append(f"[0:a]volume={vol},afade=t=in:d={min(fade, 0.2)},afade=t=out:st={max(0, cut_dur - fade):.3f}:d={fade}[av]")
    alabel = "av"
    if edits.get("music"):
        inputs += ["-stream_loop", "-1", "-i", resolve_input(edits["music"])]
        midx = sum(1 for x in inputs if x == "-i") - 1
        mvol = float(edits.get("music_volume", 0.15))
        fc.append(f"[{midx}:a]volume={mvol},atrim=0:{cut_dur:.3f},afade=t=out:st={max(0, cut_dur - 1.5):.3f}:d=1.5[mu];"
                  f"[av][mu]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[am]")
        alabel = "am"

    note("Rendering…")
    enc = (["-crf", "28", "-preset", "veryfast", "-b:a", "128k"] if preview else
           ["-crf", "18", "-preset", "medium", "-profile:v", "high", "-b:a", "192k"])
    media.run([*inputs, "-filter_complex", ";".join(fc), "-map", f"[{vlabel}]", "-map", f"[{alabel}]",
               "-t", f"{cut_dur:.3f}", "-r", fps, "-c:v", "libx264", *enc[:4 if preview else 6], "-pix_fmt", "yuv420p",
               "-c:a", "aac", enc[-2], enc[-1], "-ar", 48000, "-movflags", "+faststart", dst], timeout=3600)
    return cut_dur


async def make_previews(proj: dict, clips: list[dict], note=None, edits: dict | None = None):
    """Fast 9:16 previews (540x960, tracked + captioned) shown on the clip cards."""
    for k, c in enumerate(clips):
        if note:
            note(f"Rendering 9:16 previews {k + 1}/{len(clips)}…")
        work = CLIPS / proj["id"] / f"pv_{c['id']}"
        dst = CLIPS / proj["id"] / f"preview_{c['id']}_{int(time.time())}.mp4"
        try:
            await render_clip(proj, c, {"aspect": "9:16", **(edits or {})}, dst, work, preview=True)
            old = c.get("preview")
            c["preview"] = rel(dst)
            if old and (ROOT / old).exists():
                (ROOT / old).unlink(missing_ok=True)
        except Exception as e:
            log.warning("preview for %s failed: %s", c["id"], e)
        finally:
            shutil.rmtree(work, ignore_errors=True)


async def run_clips_preview(job):
    """Re-render one clip's 9:16 preview after editing (fast)."""
    p = dict(job.params)
    proj = load(p["project"])
    clip = next(c for c in proj["clips"] if c["id"] == p["clip"])
    edits = p.get("edits") or {}
    clip.update({k: v for k, v in edits.items() if k in ("start", "end", "words", "title", "cuts")})
    await make_previews(proj, [clip], note=lambda m: setattr(job, "message", m),
                        edits={k: v for k, v in edits.items() if k not in ("start", "end", "words", "cuts", "export", "fps")})
    save(proj)
    job.result = {"outputs": [clip["preview"]] if clip.get("preview") else [], "preview": clip.get("preview"),
                  "warnings": clip.get("render_warnings", [])}
    await resources.after_job("media")


async def run_clips_export(job):
    p = dict(job.params)
    proj = load(p["project"])
    clip = next(c for c in proj["clips"] if c["id"] == p["clip"])
    edits = p.get("edits") or {}
    clip.update({k: v for k, v in edits.items() if k in ("start", "end", "words", "title", "cuts")})
    save(proj)
    aspect = edits.get("aspect", "9:16")
    work = CLIPS / proj["id"] / f"export_{clip['id']}"
    shutil.rmtree(work, ignore_errors=True)
    dst = next_output_path(OUT_CLIPS, f"clip{aspect.replace(':', 'x')}", "mp4")
    stages = {"Cutting…": 0.05, "Tracking faces…": 0.2, "Adding captions…": 0.45, "Rendering…": 0.6}

    def note(m):
        job.message = m
        job.progress = stages.get(m, job.progress)
    cut_dur = await render_clip(proj, clip, edits, dst, work, preview=False, note=note)
    out_w, out_h = out_size(edits, False)
    clip["status"] = "exported"
    clip.setdefault("exports", []).append(rel(dst))
    save(proj)
    job.gen_id = db.add_generation(type="video", engine="ffmpeg", model=f"Clip {aspect}",
                                   prompt=(edits.get("title") or clip.get("title") or clip["text"])[:300],
                                   params={"project": proj["id"], "clip": clip["id"],
                                           **{k: v for k, v in edits.items() if k != "words"}},
                                   inputs={"video": proj["video_rel"], "range": [clip["start"], clip["end"]]},
                                   outputs=[rel(dst)], status="done", seconds=round(time.time() - job.started, 1))
    job.result = {"outputs": [rel(dst)], "seconds": round(time.time() - job.started, 1), "duration": round(cut_dur, 2),
                  "width": out_w, "height": out_h, "warnings": clip.get("render_warnings", [])}
    shutil.rmtree(work, ignore_errors=True)
    await resources.after_job("media")


async def run_clips_previews(job):
    """Create missing 9:16 previews for every clip of an existing project."""
    proj = load(job.params["project"])
    force = bool(job.params.get("force"))
    todo = [c for c in proj["clips"] if force or not c.get("preview") or not (ROOT / c["preview"]).exists()]
    await make_previews(proj, todo, note=lambda m: setattr(job, "message", m))
    save(proj)
    job.result = {"outputs": [], "previews": len(todo)}
    await resources.after_job("media")
