"""Local LLM helpers (multimodal Gemma-4 12B via llama-server): translation, clip and video analysis, metadata.

The backend is modular: anything that speaks the OpenAI chat API on 127.0.0.1 can replace llama-server.
"""
import json
import logging
import re
import base64
from pathlib import Path

import httpx

import resources
from services import SERVICES

log = logging.getLogger("llm")
LANG = {"ru": "Russian", "en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
        "pt": "Portuguese", "pl": "Polish", "tr": "Turkish", "nl": "Dutch", "uk": "Ukrainian", "zh": "Chinese",
        "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "hi": "Hindi"}


async def ensure(notify=None):
    svc = SERVICES["llm"]
    if not svc.installed:
        raise RuntimeError("The local language model is not installed (models\\llm).")
    await resources.prepare("llm", "llm:gemma4", notify=notify)
    if svc.status != "online" and not await svc.check():
        if notify:
            notify("Starting the local language model…")
        await svc.start()
        if not await svc.wait_online():
            raise RuntimeError("The local language model failed to start (see logs/llm.console.log).")
    resources.mark_loaded("llm", "Gemma-4 12B")
    return svc


async def chat(messages, schema=None, max_tokens=2048, temperature=0.3) -> str:
    svc = SERVICES["llm"]
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "top_p": 0.9,
            "cache_prompt": True,
            # Gemma-4 "thinking" is not needed for translation/scoring and would eat the token budget
            "chat_template_kwargs": {"enable_thinking": False}, "reasoning_budget": 0}
    if schema:
        body["response_format"] = {"type": "json_object", "schema": schema}
    async with httpx.AsyncClient(timeout=600) as c:
        r = await c.post(svc.url + "/v1/chat/completions", json=body)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _json(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        return json.loads(m.group(0)) if m else None


# ─────────────────────────── translation ───────────────────────────
MODE_RULES = {
    "literal": "Translate faithfully and precisely, staying close to the original wording and sentence structure.",
    "natural": ("Translate into natural, fluent everyday speech. Keep the meaning and tone; rephrase freely where "
                "a literal rendering would sound foreign."),
    "dubbing": ("You are writing the script for a professional voice-over dub. Write idiomatic SPOKEN language as a "
                "native speaker would actually say it on screen: no calques, no word-for-word structures from the "
                "source language, adapt idioms and jokes to local equivalents, prefer short clear phrases, keep the "
                "speaker's personality and emotion, and keep each line about as long as the original so it fits "
                "the same time slot (shorten rather than lengthen)."),
}
RU_DUB_RULES = (" Russian specifics: natural Russian word order (not English-style), avoid anglicisms when a common "
                "Russian word exists, avoid bureaucratic language, use «ты» or «вы» consistently according to the "
                "speaker's register, use short spoken particles (ну, вот, же) only where they sound natural, "
                "keep names in their usual Russian spelling.")


async def translate(lines: list[str], src: str, tgt: str, context: str = "", notify=None, batch=25,
                    mode: str | None = None) -> list[str]:
    """Translate spoken lines, keeping them roughly as long as the originals so dubbing timing works."""
    mode = mode or ("dubbing" if tgt == "ru" else "natural")
    rules = MODE_RULES.get(mode, MODE_RULES["natural"]) + (RU_DUB_RULES if tgt == "ru" and mode != "literal" else "")
    out = []
    schema = {"type": "object", "properties": {"t": {"type": "array", "items": {"type": "string"}}},
              "required": ["t"]}
    for i in range(0, len(lines), batch):
        chunk = lines[i:i + batch]
        if notify:
            notify(f"Translating {min(i + batch, len(lines))}/{len(lines)} lines…")
        numbered = "\n".join(f"{k + 1}. {s}" for k, s in enumerate(chunk))
        prev = "\n".join(out[-3:])
        msgs = [
            {"role": "system", "content": (
                f"You translate video dialogue from {LANG.get(src, src)} into {LANG.get(tgt, tgt)}. {rules} "
                "Never merge or split lines; keep names. "
                f"Return JSON {{\"t\": [...]}} with exactly {len(chunk)} strings in the same order.")},
            {"role": "user", "content": (f"Context: {context}\n" if context else "") +
                (f"Previous translated lines:\n{prev}\n\n" if prev else "") + f"Lines:\n{numbered}"},
        ]
        res = None
        for attempt in range(2):
            res = _json(await chat(msgs, schema=schema, max_tokens=4096))
            if res and len(res.get("t", [])) == len(chunk):
                break
            msgs[-1]["content"] += f"\n\nIMPORTANT: return exactly {len(chunk)} lines."
        t = (res or {}).get("t", [])
        if len(t) != len(chunk):   # fall back to one-by-one for this chunk
            t = []
            for s in chunk:
                r1 = _json(await chat([msgs[0], {"role": "user", "content": f"Lines:\n1. {s}"}], schema=schema))
                t.append(((r1 or {}).get("t") or [s])[0])
        out.extend(x.strip() for x in t)
    return out


# ─────────────────────────── clip scoring ───────────────────────────
SCORE_SCHEMA = {"type": "object", "properties": {"clips": {"type": "array", "items": {"type": "object", "properties": {
    "start": {"type": "integer"}, "end": {"type": "integer"},
    "hook": {"type": "integer"}, "interest": {"type": "integer"}, "context": {"type": "integer"},
    "engagement": {"type": "integer"}, "shareability": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["start", "end", "hook", "interest", "context", "engagement", "shareability", "reason"]}}},
    "required": ["clips"]}


async def find_highlights(sentences: list[dict], min_s: float, max_s: float, want: int, lang: str) -> list[dict]:
    """sentences: [{i, start, end, text}] (a window of the transcript). Returns sentence-index ranges with scores."""
    lines = "\n".join(f"[{s['i']}] ({s['start']:.0f}s) {s['text']}" for s in sentences)
    msgs = [
        {"role": "system", "content": (
            "You are an experienced short-form video editor (TikTok, Reels, Shorts). From a transcript with "
            "numbered sentences, pick the passages that work best as standalone vertical clips. A good clip: "
            "opens with a strong hook in its first sentence, contains one complete thought, needs no earlier "
            "context, and ends on a natural conclusion or punchline. Never start or end mid-thought. "
            f"Each clip must last between {min_s:.0f} and {max_s:.0f} seconds (use the timestamps). "
            "Score each 0-100 honestly: hook, interest, context (how standalone it is), engagement, shareability. "
            f"Return JSON {{\"clips\": [...]}} with up to {want} clips, each {{start, end}} as sentence numbers "
            "(inclusive) plus the five scores and a short reason written in Russian (it is shown to a Russian-speaking user).")},
        {"role": "user", "content": f"Transcript language: {LANG.get(lang, lang)}.\n\n{lines}"},
    ]
    res = _json(await chat(msgs, schema=SCORE_SCHEMA, max_tokens=3000, temperature=0.2)) or {}
    return res.get("clips", [])


async def hook_titles(text: str, lang: str, n: int = 4) -> list[str]:
    schema = {"type": "object", "properties": {"titles": {"type": "array", "items": {"type": "string"}}},
              "required": ["titles"]}
    msgs = [{"role": "system", "content": (
        f"Write {n} short on-screen hook titles (max 8 words each) in {LANG.get(lang, lang)} for a short video clip. "
        "They must be accurate to what is actually said — no clickbait, no invented claims, no ALL CAPS, "
        "no emojis. Vary the style: a question, a statement, a benefit. Return JSON {\"titles\": [...]}.")},
        {"role": "user", "content": text[:6000]}]
    res = _json(await chat(msgs, schema=schema, max_tokens=400, temperature=0.6)) or {}
    return [t.strip().strip('"') for t in res.get("titles", [])][:n]


# ─────────────────────── video / clip analysis ───────────────────────
CONTENT_SCHEMA = {"type": "object", "properties": {
    "content_summary": {"type": "string"}, "visual_summary": {"type": "string"},
    "opening_note": {"type": "string"}, "ending_note": {"type": "string"},
    "subtitle_review": {"type": "string"},
    "existing_subtitles": {"type": "string", "enum": ["visible", "not_visible", "uncertain"]},
    "context_ok": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}},
    "edit_suggestions": {"type": "array", "items": {"type": "string"}},
    "youtube": {"type": "object", "properties": {
        "title": {"type": "string"}, "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}}},
        "required": ["title", "description", "tags", "hashtags"]},
    "instagram": {"type": "object", "properties": {
        "caption": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}},
        "required": ["caption", "hashtags"]},
    "tiktok": {"type": "object", "properties": {
        "caption": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}},
        "required": ["caption", "hashtags"]}},
    "required": ["content_summary", "visual_summary", "opening_note", "ending_note", "subtitle_review", "existing_subtitles", "context_ok",
                 "issues", "edit_suggestions", "youtube", "instagram", "tiktok"]}


def _clean_content_result(value: dict | None, lang: str) -> dict:
    value = value if isinstance(value, dict) else {}
    def strings(items, limit):
        return [str(x).strip() for x in items if str(x).strip()][:limit] if isinstance(items, list) else []
    def platform(name):
        p = value.get(name) if isinstance(value.get(name), dict) else {}
        return p
    yt, ig, tt = platform("youtube"), platform("instagram"), platform("tiktok")
    return {
        "language": lang,
        "content_summary": str(value.get("content_summary") or "").strip(),
        "visual_summary": str(value.get("visual_summary") or "").strip(),
        "opening_note": str(value.get("opening_note") or "").strip(),
        "ending_note": str(value.get("ending_note") or "").strip(),
        "subtitle_review": str(value.get("subtitle_review") or "").strip(),
        "existing_subtitles": value.get("existing_subtitles") if value.get("existing_subtitles") in ("visible", "not_visible", "uncertain") else "uncertain",
        "context_ok": bool(value.get("context_ok", False)),
        "issues": strings(value.get("issues"), 6),
        "edit_suggestions": strings(value.get("edit_suggestions"), 6),
        "youtube": {"title": str(yt.get("title") or "").strip()[:100],
                    "description": str(yt.get("description") or "").strip()[:500],
                    "tags": strings(yt.get("tags"), 10), "hashtags": strings(yt.get("hashtags"), 4)},
        "instagram": {"caption": str(ig.get("caption") or "").strip()[:500],
                      "hashtags": strings(ig.get("hashtags"), 5)},
        "tiktok": {"caption": str(tt.get("caption") or "").strip()[:300],
                   "hashtags": strings(tt.get("hashtags"), 5)},
    }


async def analyze_content(transcript: str, frames: list[dict], lang: str, full_video: bool = False) -> dict:
    """Analyze transcript plus ordered video stills and create evidence-based social metadata locally."""
    language = LANG.get(lang, lang or "the same language as the transcript")
    transcript = transcript or ""
    if len(transcript) > 12000:
        # Keep speech from the start, middle and end of long inputs instead of silently analyzing only the opening.
        width, sections = 1450, 7
        last_start = max(0, len(transcript) - width)
        positions = [round(i * last_start / (sections - 1)) for i in range(sections)]
        transcript = "\n\n".join(f"[Transcript sample {i + 1}] {transcript[a:a + width]}"
                                   for i, a in enumerate(positions))
    system = (
        "You are a careful video editor and social metadata writer. The user gives you a transcript and a few "
        "timestamped still frames in chronological order from the SAME video. Infer only what these inputs support. "
        "The stills are samples, not the whole video; do not claim motion or events not visible in the samples. "
        "Write the summary, notes, title, captions, hashtags and tags primarily in " + language + ". "
        "Keep titles natural, concise, accurate, and specific to this material. Do not use generic clickbait. "
        "Use a short description/caption. Use a few highly relevant hashtags and tags; no keyword spam. "
        "YouTube tags are plain keywords without #; hashtags include #. "
        "For Islamic material, never invent Quran or Hadith text, references, scholar names, attributions, or rulings. "
        "If a speaker or attribution is unclear, omit it. Do not alter or overstate the religious meaning. "
        "Flag unclear, awkward, unsupported, or context-dependent content in issues/edit_suggestions. "
        "Set existing_subtitles to visible only when readable spoken-word captions are clearly present across multiple supplied frames; "
        "not_visible only when the sampled frames give reasonable evidence that no burned-in spoken captions are present; "
        "otherwise use uncertain. Do not mistake logos, watermarks, decorative titles, or Arabic source text for subtitles. "
        "If subtitles are visibly burned into sampled frames, check only obvious readability or text/transcript mismatches; "
        "otherwise say there is not enough visual evidence to review subtitles. "
        "Do not pretend to judge audio fidelity from still images or transcript. If the transcript itself is unclear, say so. "
        "Return only the requested JSON fields."
    )
    image_rule = ("Review every supplied frame in order; describe only visible details and note if the sampled stills "
                  "do not establish an event or speaker identity.") if frames else (
                  "No video frames were available. Set visual_summary to an empty string and make no visual claims.")
    user_content = [{"type": "text", "text": (
        ("Analyze this complete long video using representative samples." if full_video else
         "Analyze this short clip as a standalone social video.") +
        f"\nTranscript language: {language}.\nTranscript:\n{transcript[:12000]}\n\n"
        "For the opening and ending, judge only the supplied transcript and timestamped frames. "
        f"If evidence is insufficient, use cautious neutral wording and list the uncertainty as an issue. {image_rule}"
    )}]
    for frame in frames:
        path = Path(frame["path"])
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        user_content.append({"type": "text", "text": f"Frame at {float(frame.get('time', 0)):.1f} seconds:"})
        user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
    result = _json(await chat([{"role": "system", "content": system},
                               {"role": "user", "content": user_content}],
                              schema=CONTENT_SCHEMA, max_tokens=1200, temperature=0.25))
    cleaned = _clean_content_result(result, lang)
    cleaned["visual_review_available"] = bool(frames)
    if not frames:
        cleaned["visual_summary"] = ""
    return cleaned


EDITOR_ANALYSIS_SCHEMA = {"type": "object", "properties": {
    "content_summary": {"type": "string"}, "visual_summary": {"type": "string"},
    "opening_note": {"type": "string"}, "ending_note": {"type": "string"},
    "existing_subtitles": {"type": "string", "enum": ["visible", "not_visible", "uncertain"]},
    "issues": {"type": "array", "items": {"type": "string"}},
    "edit_suggestions": {"type": "array", "items": {"type": "string"}},
    "frame_notes": {"type": "array", "items": {"type": "object", "properties": {
        "frame_index": {"type": "integer"}, "note": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]}},
        "required": ["frame_index", "note", "confidence"]}},
}, "required": ["content_summary", "visual_summary", "opening_note", "ending_note", "existing_subtitles", "issues", "edit_suggestions", "frame_notes"]}


async def analyze_for_editing(transcript: str, frames: list[dict], lang: str,
                              scene_changes: list[dict] | None = None,
                              scene_detection_available: bool = False) -> dict:
    """Return only evidence-grounded editing notes; do not prepare social titles or metadata on import."""
    language = LANG.get(lang, lang or "the same language as the transcript")
    review_transcript = transcript or ""
    if len(review_transcript) > 10_000:
        width, sections = 1350, 7
        last_start = max(0, len(review_transcript) - width)
        positions = [round(i * last_start / (sections - 1)) for i in range(sections)]
        review_transcript = "\n\n".join(
            f"[Transcript sample {i + 1} of {sections}; intermediate speech is omitted] "
            f"{review_transcript[start:start + width]}"
            for i, start in enumerate(positions)
        )
    system = (
        "You are a conservative local video editor analyzing a transcript and sparse, timestamped still frames. "
        "Write every user-facing text field in " + language + ". Never invent events, dialogue, identities, religious attributions, or claims. "
        "Treat stills as samples: do not claim to have reviewed every frame or heard audio quality. "
        "Recommend reversible, restrained edits only; preserve the speaker's meaning and natural pacing. "
        "Set existing_subtitles=visible only when readable spoken-word captions are clearly visible across multiple frames; "
        "not_visible only when sampled frames give reasonable evidence that none are burned in; otherwise uncertain. "
        "Do not mistake logos, watermarks, title cards, or Arabic source text for subtitles. "
        "For frame_notes, use only supplied frame_index values, describe visible composition in " + language + ", and do not mistake a pose change for a new scene. "
        "Do not transcribe, quote, translate, or infer any on-screen subtitle text from an image. "
        "Mark confidence low when a still does not clearly support the note. Do not turn a scene-change score into a certainty claim. "
        "Return only the requested JSON, with concise notes and no social-media titles, descriptions, tags, or hashtags."
    )
    changes = []
    for change in scene_changes or []:
        try:
            at, score = float(change.get("time")), float(change.get("score"))
        except (AttributeError, TypeError, ValueError):
            continue
        if 0 <= at < 86400 and 0 <= score <= 1:
            changes.append({"time": round(at, 2), "score": round(score, 3)})
    candidate_frame_indices = [index for index, frame in enumerate(frames, start=1)
                               if any(0 <= float(frame.get("time", 0)) - item["time"] <= 1.25 for item in changes)]
    content = [{"type": "text", "text": (
        f"Transcript language: {language}. Transcript or start/middle/end samples:\n{review_transcript}\n\n"
        f"Low-rate FFmpeg scene scan completed: {bool(scene_detection_available)}. "
        f"Shot-change candidates (source seconds and technical difference score; not confirmed editorial cuts): {json.dumps(changes, ensure_ascii=False)}\n"
        f"Frames that fall shortly after detected shot-change candidates: {candidate_frame_indices}. "
        f"Write every textual answer in {language}. Do not OCR or quote captions visible in frames; subtitle assessment is handled separately.\n"
        "Review the opening, ending, framing, and only obvious issues visible in the supplied sample frames. "
        "If transcript passages are sampled with omissions, do not infer the missing narrative between them. "
        "Return a frame_note only for a listed post-cut frame index. If the list is empty, return an empty frame_notes array. "
        "Use one short note in the requested language per listed index. These notes describe sparse stills and are not a complete scene-by-scene review. "
        "If no frames or the evidence is weak, state that uncertainty instead of guessing."
    )}]
    for index, frame in enumerate(frames, start=1):
        path = Path(frame["path"])
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "text", "text": f"Frame index {index}, source time {float(frame.get('time', 0)):.2f} seconds:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
    value = _json(await chat([{"role": "system", "content": system}, {"role": "user", "content": content}],
                             schema=EDITOR_ANALYSIS_SCHEMA, max_tokens=1000, temperature=0.1)) or {}
    def strings(items):
        return [str(item).strip() for item in items if str(item).strip()][:6] if isinstance(items, list) else []
    subtitle_state = value.get("existing_subtitles")
    frame_notes = []
    seen_frames = set()
    for item in value.get("frame_notes", []) if isinstance(value.get("frame_notes"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("frame_index"))
        except (TypeError, ValueError):
            continue
        note = str(item.get("note") or "").strip()[:360]
        if index < 1 or index > len(frames) or index not in candidate_frame_indices or index in seen_frames or not note:
            continue
        if lang in ("ru", "uk", "bg", "be", "mk", "sr") and not re.search(r"[А-Яа-яЁёІіЇїЄєЃѓЌќЉљЊњЋћЏџ]", note):
            continue
        seen_frames.add(index)
        confidence = item.get("confidence") if item.get("confidence") in ("low", "medium", "high") else "low"
        frame_notes.append({"frame_index": index, "time": round(float(frames[index - 1].get("time", 0)), 2),
                            "note": note, "confidence": confidence})
    return {
        "language": lang, "content_summary": str(value.get("content_summary") or "").strip()[:1200],
        "visual_summary": str(value.get("visual_summary") or "").strip()[:1200] if frames else "",
        "opening_note": str(value.get("opening_note") or "").strip()[:400],
        "ending_note": str(value.get("ending_note") or "").strip()[:400],
        "existing_subtitles": subtitle_state if subtitle_state in ("visible", "not_visible", "uncertain") else "uncertain",
        "issues": strings(value.get("issues")), "edit_suggestions": strings(value.get("edit_suggestions")),
        "frame_notes": frame_notes,
        "scene_changes": changes,
        "scene_detection_available": bool(scene_detection_available),
        "visual_review_available": bool(frames),
    }


EXPORT_REVIEW_SCHEMA = {"type": "object", "properties": {
    "notes": {"type": "array", "items": {"type": "object", "properties": {
        "frame_index": {"type": "integer"},
        "category": {"type": "string", "enum": ["framing", "captions", "visual_artifact"]},
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "note": {"type": "string"}},
        "required": ["frame_index", "category", "severity", "note"]}}
}, "required": ["notes"]}


async def review_export_frames(frames: list[dict], lang: str = "ru",
                               video_width: int | None = None, video_height: int | None = None) -> dict:
    """Inspect a small set of exported stills for clear visual defects; never claim full-video review."""
    language = LANG.get(lang, lang or "Russian")
    system = (
        "You are a cautious visual quality reviewer for an exported video. Review only the supplied sparse still frames, "
        "in chronological order; they are not the whole video and do not show motion or audio. Write all notes in " + language + ". "
        "Report only clearly visible technical problems: the main person/object is obviously cut off or badly framed, "
        "burned-in captions are clipped by the true video edge or visibly obscured, or there is a severe black/frozen-looking/compression artifact. "
        "The stills are fitted inside a square review image and may have letterbox padding; judge distances against the actual video rectangle, not the square canvas. "
        "A caption in the lower third is normal and is not a defect. Flag a caption for edge placement only if its letters are cut off or come within about 3 percent of the actual video frame edge; "
        "if it is fully visible with at least 6 percent padding, return no caption note. Do not flag a placement merely because text is below the frame midpoint. "
        f"The actual output video is {video_width or 'unknown'} by {video_height or 'unknown'} pixels. "
        "For 9:16 portrait footage, a medium close-up of a speaker is a normal intentional composition: the background, shoulders, hands, or clothing may be cropped. "
        "Do not call a portrait crop defective when the entire face and head are visible with clear padding from the frame edges. "
        "Report a framing problem only if the face/head is visibly cut by an edge, the person is largely outside the frame, or a clearly important object is unintentionally clipped. "
        "Do not transcribe, quote, translate, or judge the wording of subtitles. Do not infer religious meaning, identities, audio quality, "
        "motion continuity, or what happens between samples. Do not call an intentional short fade/transition a defect. "
        "If uncertain or no clear problem is visible, return no note for that frame. Keep notes short and actionable. "
        "Return only JSON."
    )
    content = [{"type": "text", "text": (
        f"Inspect these {min(len(frames), 10)} sample frames from the exported timeline. "
        "Use frame_index exactly as numbered and do not invent timestamps. Sparse stills cannot prove the whole export is clean."
    )}]
    for index, frame in enumerate(frames[:10], start=1):
        path = Path(frame["path"])
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "text", "text": f"Export frame {index}, timeline time {float(frame.get('time', 0)):.2f} seconds:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
    value = _json(await chat([{"role": "system", "content": system}, {"role": "user", "content": content}],
                             schema=EXPORT_REVIEW_SCHEMA, max_tokens=500, temperature=0.1)) or {}
    notes, seen = [], set()
    for item in value.get("notes", []) if isinstance(value.get("notes"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("frame_index"))
        except (TypeError, ValueError):
            continue
        note = str(item.get("note") or "").strip()[:280]
        if index < 1 or index > min(len(frames), 10) or index in seen or not note:
            continue
        if lang in ("ru", "uk", "bg", "be", "mk", "sr") and not re.search(r"[А-Яа-яЁёІіЇїЄєЃѓЌќЉљЊњЋћЏџ]", note):
            continue
        category = item.get("category") if item.get("category") in ("framing", "captions", "visual_artifact") else "visual_artifact"
        severity = item.get("severity") if item.get("severity") in ("low", "medium", "high") else "low"
        seen.add(index)
        notes.append({"frame_index": index, "time": round(float(frames[index - 1].get("time", 0)), 2),
                      "category": category, "severity": severity, "note": note})
    return {"available": True, "language": lang, "sampled_frames": min(len(frames), 10), "notes": notes}
