"""Timeline assembly, local captioning, sound effects and AI editing assistance."""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import captions
import db
import llm
import media
import jobs
import resources
from settings import DATA, MODELS, OUT_AUDIO, OUT_VIDEO_EDIT, ROOT

log = logging.getLogger("video_editor")
WORK = DATA / "editor-work"
WORK.mkdir(parents=True, exist_ok=True)

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "operations": {"type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["split", "trim", "transition", "color", "volume", "duck_music", "audio_track", "format", "scene", "analyze", "captions", "sfx", "face_blur", "remove_pauses", "upscale", "interpolate", "none"]},
            "at_seconds": {"type": "number"}, "after_seconds": {"type": "number"}, "start_seconds": {"type": "number"},
            "end_seconds": {"type": "number"}, "target_index": {"type": "integer"},
            "transition_type": {"type": "string", "enum": ["dissolve", "fade", "fadeblack", "wipeleft", "wiperight", "slideright", "slideleft", "circleopen", "smoothleft"]},
            "duration": {"type": "number"}, "brightness": {"type": "number"}, "contrast": {"type": "number"},
            "saturation": {"type": "number"}, "temperature": {"type": "number"}, "tint": {"type": "number"},
            "original_volume": {"type": "number"}, "music_volume": {"type": "number"}, "enabled": {"type": "boolean"},
            "audio_track": {"type": "string", "enum": ["original", "music", "sfx"]},
            "track_mode": {"type": "string", "enum": ["mute", "solo"]},
            "count": {"type": "integer"}, "target_height": {"type": "integer"}, "multiplier": {"type": "integer"}, "target_fps": {"type": "number"},
            "caption_style": {"type": "string", "enum": ["minimal", "bold", "creator", "movie", "subtitle", "opus", "podcast", "clean", "reels", "neon", "cinematic", "boxed", "outline"]},
            "caption_highlight": {"type": "boolean"},
            "caption_animation": {"type": "string", "enum": ["none", "pop", "bounce", "fade"]},
            "effect_prompt": {"type": "string"},
            "aspect": {"type": "string", "enum": ["source", "9:16", "1:1", "16:9"]}, "scene_prompt": {"type": "string"}
        }, "required": ["type"]}}
    },
    "required": ["reply", "operations"],
}
TRANSITIONS = {"dissolve", "fade", "fadeblack", "wipeleft", "wiperight", "slideright", "slideleft", "circleopen", "smoothleft"}


def _audio_level_warnings(peak: str | None, mean: str | None, expected_audio: bool,
                          continuous_audio: bool, transient_audio: bool) -> list[str]:
    warnings = []
    try:
        peak_db = float(peak) if peak is not None and peak != "-inf" else None
    except (TypeError, ValueError):
        peak_db = None
    try:
        mean_db = float(mean) if mean is not None and mean != "-inf" else float("-inf")
    except (TypeError, ValueError):
        mean_db = None
    if peak_db is not None and peak_db >= -0.1:
        warnings.append("Пики звука близки к цифровому клиппингу; проверьте громкость на слух.")
    if expected_audio and mean == "-inf":
        warnings.append("Звуковая дорожка найдена, но сигнал выглядит полностью тихим.")
    if peak_db is not None and mean_db is not None:
        if expected_audio and continuous_audio and peak_db <= -24 and mean_db <= -32:
            warnings.append("Ожидаемая речь или музыка звучит очень тихо; проверьте уровень микса.")
        elif expected_audio and transient_audio and not continuous_audio and peak_db <= -36:
            warnings.append("SFX дорожка найдена, но её пик очень тихий; проверьте громкость эффекта.")
    return warnings


def _render_quality_check(path: Path, expected_duration: float, expected_width: int, expected_height: int,
                          transitions: list[dict], review_dir: Path | None = None,
                          audio_expectation: dict | None = None) -> dict:
    """Decode and inspect an exported timeline before reporting it as ready."""
    info = media.probe(path)
    audio_expectation = audio_expectation if isinstance(audio_expectation, dict) else {}
    expected_audio = bool(audio_expectation.get("expected", info.get("has_audio")))
    continuous_audio = bool(audio_expectation.get("continuous", expected_audio))
    transient_audio = bool(audio_expectation.get("transient", False))
    warnings = []
    duration_error = abs(float(info.get("duration") or 0) - expected_duration)
    video_ok = bool(info.get("has_video") and info.get("width") == expected_width
                    and info.get("height") == expected_height and duration_error <= 0.75)
    audio_ok = bool(info.get("has_audio")) or not expected_audio
    if not video_ok:
        warnings.append("Проверьте видеодорожку, разрешение и длительность экспорта.")
    if expected_audio and not info.get("has_audio"):
        warnings.append("В экспортированном файле не найдена аудиодорожка.")

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    decode = subprocess.run([str(media.FFMPEG), "-v", "error", "-i", str(path), "-f", "null", "-"],
                            capture_output=True, text=True, creationflags=flags, timeout=1800)
    decode_ok = decode.returncode == 0
    if not decode_ok:
        warnings.append("FFmpeg нашёл ошибки декодирования в готовом файле.")

    black_intervals = []
    black = subprocess.run([str(media.FFMPEG), "-hide_banner", "-loglevel", "info", "-i", str(path),
                            "-vf", "blackdetect=d=0.75:pix_th=0.08", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, creationflags=flags, timeout=1800)
    for match in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", black.stderr):
        start, end, length = (float(match.group(i)) for i in (1, 2, 3))
        if length >= 0.75:
            black_intervals.append({"start": round(start, 2), "end": round(end, 2), "duration": round(length, 2)})
    if black_intervals:
        warnings.append("Обнаружены длительные тёмные участки; сверьте их с намеренными переходами.")

    audio = subprocess.run([str(media.FFMPEG), "-hide_banner", "-loglevel", "info", "-i", str(path),
                            "-vn", "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, creationflags=flags, timeout=1800) if audio_ok else None
    audio_text = audio.stderr if audio else ""
    peak_match = re.search(r"max_volume:\s*(-?inf|-?[\d.]+) dB", audio_text)
    mean_match = re.search(r"mean_volume:\s*(-?inf|-?[\d.]+) dB", audio_text)
    peak = peak_match.group(1) if peak_match else None
    mean = mean_match.group(1) if mean_match else None
    warnings.extend(_audio_level_warnings(peak, mean, expected_audio, continuous_audio, transient_audio))

    # A few small stills make visual spot-checks quick without claiming a full-frame review.
    review_frames = []
    if review_dir is not None and info.get("duration"):
        review_dir.mkdir(parents=True, exist_ok=True)
        actual_duration = float(info["duration"])
        times = [0.5, actual_duration * 0.25, actual_duration * 0.5,
                 actual_duration * 0.75, max(0.05, actual_duration - 0.5)]
        for transition in transitions:
            try:
                at = float(transition.get("at", 0))
                length = float(transition.get("duration", 0))
            except (AttributeError, TypeError, ValueError):
                continue
            times.extend((at + length * 0.25, at + length * 0.75))
        selected_times = []
        for at in sorted(max(0.02, min(actual_duration - 0.02, value)) for value in times):
            if all(abs(at - other) >= 0.18 for other in selected_times):
                selected_times.append(at)
            if len(selected_times) >= 10:
                break
        for index, at in enumerate(selected_times, start=1):
            frame_path = review_dir / f"frame_{index:02d}.jpg"
            try:
                media.run(["-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
                           "-vf", "scale=480:480:force_original_aspect_ratio=decrease", "-q:v", "5", frame_path],
                          timeout=120)
                if frame_path.exists() and frame_path.stat().st_size > 512:
                    review_frames.append({"time": round(at, 2), "path": jobs.rel(frame_path)})
            except Exception as exc:
                log.warning("Could not extract export review frame at %.2fs from %s: %s", at, path, exc)

    return {
        "passed": video_ok and audio_ok and decode_ok,
        "video_ok": video_ok,
        "audio_ok": audio_ok,
        "decode_ok": decode_ok,
        "duration": round(float(info.get("duration") or 0), 2),
        "expected_duration": round(expected_duration, 2),
        "width": info.get("width"),
        "height": info.get("height"),
        "fps": info.get("fps"),
        "audio_peak_db": peak,
        "audio_mean_db": mean,
        "audio_expected": expected_audio,
        "black_intervals": black_intervals,
        "transitions": transitions,
        "review_frames": review_frames,
        "visual_review_available": bool(review_frames),
        "warnings": warnings,
    }


def _clamp(value, low, high, default):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _clean_grade(data):
    return {
        "brightness": _clamp(data.get("brightness"), -0.25, 0.25, 0),
        "contrast": _clamp(data.get("contrast"), 0.5, 1.8, 1),
        "saturation": _clamp(data.get("saturation"), 0, 1.8, 1),
        "temperature": _clamp(data.get("temperature"), -1, 1, 0),
        "tint": _clamp(data.get("tint"), -1, 1, 0),
    }


def _fallback(prompt):
    p = prompt.casefold()
    grade = {"brightness": 0, "contrast": 1, "saturation": 1, "temperature": 0, "tint": 0}
    if any(x in p for x in ("черно-бел", "чёрно-бел", "black and white", "grayscale", "монохром")):
        grade["saturation"] = 0
        note = "Применён чёрно-белый стиль."
    elif any(x in p for x in ("тепл", "warm", "золот", "golden")):
        grade.update(temperature=0.55, saturation=1.05)
        note = "Цветовая температура сделана теплее."
    elif any(x in p for x in ("холод", "cool", "син", "blue")):
        grade.update(temperature=-0.55, saturation=1.03)
        note = "Цветовая температура сделана холоднее."
    elif any(x in p for x in ("ярк", "bright", "светле", "lighten")):
        grade.update(brightness=0.08, contrast=1.04)
        note = "Изображение немного осветлено."
    elif any(x in p for x in ("контраст", "contrast")):
        grade["contrast"] = 1.15
        note = "Немного усилен контраст."
    elif any(x in p for x in ("насыщ", "vibrant", "цвета ярче", "more color")):
        grade["saturation"] = 1.18
        note = "Усилена насыщенность цветов."
    elif any(x in p for x in ("цвет", "color", "colour")):
        grade.update(contrast=1.04, saturation=1.08)
        note = "Применено лёгкое естественное усиление цвета."
    else:
        return {"action": "scene", "grade": grade, "scene_prompt": prompt[:500],
                "note": "Запрос передан для генеративного редактирования сцены."}
    return {"action": "grade", "grade": grade, "scene_prompt": "", "note": note}


def _prompt_times(text):
    import re
    values = []
    for m in re.finditer(r"(?<!\d)(\d{1,2}):(\d{2}(?:[.,]\d+)?)(?!\d)", text):
        values.append(int(m.group(1)) * 60 + float(m.group(2).replace(",", ".")))
    if values:
        return values
    pattern = r"(\d+(?:[.,]\d+)?)\s*(?:[- ]?(?:й|я|ю|е)\s*)?секунд[а-яё]*|(\d+(?:[.,]\d+)?)\s*(?:сек\.?|seconds?|secs?|\bs\b)"
    for m in re.finditer(pattern, text, re.I):
        raw = m.group(1) or m.group(2)
        if raw:
            values.append(float(raw.replace(",", ".")))
    return values


def _fallback_plan(prompt, context):
    import re
    text = prompt.casefold()
    ops = []
    base_reply = None
    def _result(reply, extra=None):
        return {"reply": (base_reply + "\n" + reply if base_reply else reply), "operations": ops + (extra or [])}

    if (re.search(r"\bshorts?\b|шортс|шорты", text)
            or re.search(r"\b(?:make|create|generate|find|cut)\b.{0,35}\bclips?\b|\bclips?\b.{0,25}\bfrom\b", text)
            or re.search(r"(?:сделай|создай|найди|нареж\w*|нарез\w*)\w*.{0,30}(?:клип|шорт)", text)):
        return {"reply": "Подбор и создание Shorts находится в отдельном разделе «Видео · Клипы». Откройте его, чтобы анализировать длинное видео и собирать новые фрагменты. Здесь редактируются уже добавленные клипы и исходники.", "operations": []}

    wants_face_blur = bool(re.search(r"(?:\bblur\w*.{0,30}\bfaces?\b|\bfaces?.{0,30}\bblur\w*\b|разм\w*.{0,25}лиц\w*|лиц\w*.{0,25}разм\w*|анонимиз\w*.{0,25}лиц\w*)", text))
    if wants_face_blur:
        return {"reply": "Запускаю локальное обнаружение и размытие лиц на выбранном исходнике. Результат заменит источник в текущем монтаже; проверьте его перед экспортом.",
                "operations": [{"type": "face_blur", "mode": "gaussian"}]}

    # Route common producer commands as a single, ordered plan.  The UI executes
    # each operation through the existing local analysis/rendering tools.
    routed = []
    wants_pause_trim = any(w in text for w in ("убери паузы", "вырежи паузы", "сократи паузы", "remove pauses", "remove silence", "cut silence"))
    if wants_pause_trim:
        routed.append({"type": "remove_pauses"})
    wants_upscale = any(w in text for w in ("upscale", "апскейл", "увеличь разрешение", "улучши разрешение", "1080p", "2160p", "4k"))
    if wants_upscale:
        target_height = 2160 if any(w in text for w in ("2160p", "4k", "4к")) else 1080
        routed.append({"type": "upscale", "target_height": target_height})
    wants_interpolate = any(w in text for w in ("60fps", "60 fps", "60 кадров", "плавность", "rife", "frame interpolation"))
    if wants_interpolate:
        routed.append({"type": "interpolate", "target_fps": 60})
    wants_music_duck = bool(re.search(r"(?:приглуш\w*.{0,35}(?:музык\w*|music).{0,30}(?:реч\w*|голос\w*|speech|voice)|(?:music ducking|duck music).{0,30}(?:speech|voice)|автоприглушен\w*.{0,20}(?:музык\w*|music))", text))
    if wants_music_duck:
        disable_duck = any(phrase in text for phrase in ("не приглушать музыку", "отключи автоприглушение", "выключи автоприглушение", "disable ducking", "turn off ducking", "don't duck music"))
        routed.append({"type": "duck_music", "enabled": not disable_duck})
    audio_track = ("sfx" if any(w in text for w in ("sfx", "звуковую дорожку эффект", "дорожку эффектов", "дорожку sfx")) else
                   "music" if any(w in text for w in ("музык", "music", "фоновую дорожку")) else
                   "original" if any(w in text for w in ("оригинальн", "исходн", "оригинал", "original audio", "source audio", "голос видео")) else None)
    wants_solo = any(w in text for w in ("solo", "соло", "оставь только"))
    clear_solo = any(w in text for w in ("unsolo", "сними solo", "сними соло", "убери solo", "верни общий микс"))
    wants_mute = any(w in text for w in ("mute", "turn off", "disable", "без звука", "выключи", "отключи", "заглуши"))
    wants_unmute = any(w in text for w in ("unmute", "turn on", "enable", "включи", "сними заглушение"))
    if audio_track and (wants_solo or clear_solo or wants_mute or wants_unmute):
        mode = "solo" if wants_solo or clear_solo else "mute"
        enabled = (not clear_solo) if mode == "solo" else (not wants_unmute)
        routed.append({"type": "audio_track", "audio_track": audio_track, "track_mode": mode, "enabled": enabled})
    elif clear_solo:
        current_solos = context.get("audio_solos") if isinstance(context.get("audio_solos"), dict) else {}
        for track in ("original", "music", "sfx"):
            if current_solos.get(track):
                routed.append({"type": "audio_track", "audio_track": track, "track_mode": "solo", "enabled": False})
    wants_caption = any(w in text for w in ("субтитр", "подпис", "caption", "subtitle"))
    if wants_caption:
        caption_style = "cinematic" if any(w in text for w in ("кино", "cinematic")) else \
            "neon" if any(w in text for w in ("неон", "neon")) else \
            "boxed" if any(w in text for w in ("подлож", "плашк", "фон под", "boxed")) else \
            "reels" if any(w in text for w in ("ярк", "creator", "reels")) else "creator"
        routed.append({"type": "captions", "caption_style": caption_style,
                       "caption_highlight": not any(w in text for w in ("без подсветки", "без выделения", "no highlight"))})
    sfx_subject = any(w in text for w in ("sfx", "звуковой эффект", "эффект звука", "whoosh", "sound effect"))
    sfx_creation = any(w in text for w in ("добав", "созда", "сгенер", "размест", "постав", "add", "create", "generat", "place", "insert", "whoosh"))
    sfx_negated = any(w in text for w in ("не добав", "не созда", "не генер", "don't add", "do not add", "no sfx", "без sfx", "without sfx", "без звукового эффекта"))
    wants_sfx = sfx_subject and sfx_creation and not sfx_negated
    if wants_sfx:
        sound_prompt = "subtle cinematic transition whoosh" if any(w in text for w in ("sfx", "звуков", "эффект звука", "sound effect")) else text[:240]
        routed.append({"type": "sfx", "effect_prompt": sound_prompt, "at_seconds": _clamp(context.get("playhead_seconds"), 0, 86400, 0), "duration": 3})
    if routed:
        labels = []
        if wants_pause_trim: labels.append("сокращу длинные паузы по таймкодам речи")
        if wants_caption: labels.append("подготовлю субтитры")
        if wants_sfx: labels.append("создам и размещу SFX на шкале")
        if wants_upscale: labels.append(f"запущу локальное увеличение разрешения до {target_height}p")
        if wants_interpolate: labels.append("запущу локальное повышение частоты кадров")
        if wants_music_duck: labels.append("настрою приглушение музыки под речь" if not disable_duck else "выключу приглушение музыки под речь")
        if audio_track and (wants_solo or clear_solo or wants_mute or wants_unmute):
            action = "оставлю в Solo" if mode == "solo" and enabled else "сниму Solo" if mode == "solo" else "выключу звук" if enabled else "включу звук"
            labels.append(f"{audio_track}: {action}")
        return _result("План монтажа: " + "; ".join(labels) + ".", routed)

    times = _prompt_times(text)
    explicit_transition_duration = re.search(r"(?:длительност(?:ью|ь)|duration|lasting|for)\s*(?:на|в)?\s*(\d+(?:[.,]\d+)?)\s*(?:с|сек|second|sec)", text)
    time_values = list(times)
    requested_transition_duration = None
    if explicit_transition_duration:
        requested_transition_duration = _clamp(explicit_transition_duration.group(1).replace(",", "."), .15, 2, .7)
        duration_value = float(explicit_transition_duration.group(1).replace(",", "."))
        time_values = [value for value in time_values if abs(value - duration_value) > .001]
    total = _clamp(context.get("timeline_duration"), 0, 86400, 0)
    playhead = _clamp(context.get("playhead_seconds"), 0, 86400, 0)
    split_requested = bool(re.search(r"разреж|раздел|рассек|split|cut\s+at", text))
    transition_requested = bool(re.search(r"переход|transition|cross.?dissolve|wipe|растворен|затемнен", text))
    time_at = time_values[0] if time_values else None

    if split_requested:
        if time_at is None and any(w in text for w in ("здесь", "тут", "сейчас", "этой секунде", "курсор", "playhead", "cursor", "this point", "here", "current")):
            time_at = playhead
        if time_at is None:
            return {"reply": "Поставьте курсор воспроизведения на нужный момент или укажите секунды, например: «разрежь на 12 секунд».", "operations": []}
        if time_at <= 0.05 or (total and time_at >= total - 0.05):
            return {"reply": "В этой точке нельзя создать разрез: выберите момент внутри клипа.", "operations": []}
        ops.append({"type": "split", "at_seconds": round(time_at, 3)})

    if transition_requested:
        style = "dissolve"
        if any(x in text for x in ("затемнен", "fade to black", "черный экран", "чёрный экран")):
            style = "fadeblack"
        elif any(x in text for x in ("fade", "плавное появление")):
            style = "fade"
        elif any(x in text for x in ("wipe", "свайп", "шторк")):
            style = "wipeleft"
        elif any(x in text for x in ("сдвиг вправо", "slide right", "slideright")):
            style = "slideright"
        elif any(x in text for x in ("сдвиг влево", "slide left", "slideleft")):
            style = "slideleft"
        elif any(x in text for x in ("круг", "circle")):
            style = "circleopen"
        duration = requested_transition_duration or 0.7
        op = {"type": "transition", "transition_type": style, "duration": duration}
        if split_requested and time_at is not None:
            op["after_seconds"] = round(time_at, 3)
        elif time_at is not None:
            op["after_seconds"] = round(time_at, 3)
        elif any(w in text for w in ("здесь", "тут", "сейчас", "на стыке", "курсор", "playhead", "cursor", "this point", "here", "current")):
            op["after_seconds"] = round(playhead, 3)
        else:
            op["target_index"] = int(_clamp(context.get("selected_segment_index"), -1, 79, -1))
        ops.append(op)

    if split_requested or transition_requested:
        detail = []
        if split_requested:
            detail.append("разрез")
        if transition_requested:
            detail.append("переход «" + style + "»")
        base_reply = "Добавляю в проект: " + " и ".join(detail) + "."

    sfx_requested = wants_sfx
    caption_requested = any(w in text for w in ("субтитр", "подпис", "caption", "subtitle", "captions"))
    if caption_requested:
        if any(w in text for w in ("кино", "кинемат", "cinematic", "фильм")):
            style = "cinematic"
        elif any(w in text for w in ("неон", "фиолет", "neon", "purple")):
            style = "neon"
        elif any(w in text for w in ("подлож", "фон", "boxed", "плашк")):
            style = "boxed"
        elif any(w in text for w in ("чист", "минимал", "clean", "minimal")):
            style = "clean"
        elif any(w in text for w in ("жирн", "ярк", "reels", "блогер", "creator")):
            style = "reels"
        elif any(w in text for w in ("обычн", "прост", "белые", "без подсветки")):
            style = "minimal"
        else:
            style = "creator"
        animation = "bounce" if any(w in text for w in ("bounce", "прыга", "пружин")) else \
            "pop" if any(w in text for w in ("анимац", "animated", "pop", "динамич", "выразител")) else "none"
        ops.append({"type": "captions", "caption_style": style, "caption_animation": animation,
                    "caption_highlight": not any(w in text for w in ("без подсветки", "без выделения", "no highlight"))})
        base_reply = (base_reply + " " if base_reply else "") + "Настраиваю стиль субтитров «" + style + "»; если расшифровки ещё нет, редактор создаст её локально через Whisper."
        if not split_requested and not transition_requested and not sfx_requested:
            return _result("Стиль субтитров будет применён при предпросмотре и экспорте.")

    if sfx_requested:
        at = time_at if time_at is not None else playhead
        ops.append({"type": "sfx", "effect_prompt": text[:300], "at_seconds": round(at, 3), "duration": 4})
        base_reply = (base_reply + " " if base_reply else "") + "Создам короткий звуковой эффект и поставлю его у курсора на шкале."
        if not split_requested and not transition_requested and not caption_requested:
            return _result("Для генерации используется локальная модель Stable Audio Small-SFX.")

    if any(w in text for w in ("анализ", "проанализ", "analyze", "analyse", "analysis")):
        return _result("Запускаю анализ выбранного исходного видео.", [{"type": "analyze"}])

    if any(w in text for w in ("вертикаль", "9:16")):
        return _result("Переключаю кадр на вертикальный формат 9:16.", [{"type": "format", "aspect": "9:16"}])
    if any(w in text for w in ("квадрат", "1:1", "square")):
        return _result("Переключаю кадр на квадратный формат 1:1.", [{"type": "format", "aspect": "1:1"}])
    if any(w in text for w in ("горизонталь", "16:9", "широкий формат", "landscape")):
        return _result("Переключаю кадр на широкий формат 16:9.", [{"type": "format", "aspect": "16:9"}])
    if any(w in text for w in ("исходный формат", "как в исходнике", "сбрось формат", "reset aspect")):
        return _result("Возвращаю формат кадра исходного видео.", [{"type": "format", "aspect": "source"}])

    if any(w in text for w in ("громк", "звук", "тише", "громче", "без звука", "mute", "volume")):
        current_original = _clamp(context.get("original_volume"), 0, 2, 1)
        current_music = _clamp(context.get("music_volume"), 0, .6, .12)
        import re
        percent = re.search(r"(\d{1,3})\s*%", text)
        music_intent = "музык" in text or "music" in text
        if music_intent:
            if any(w in text for w in ("без звука", "выключи", "mute", "тишина")):
                target_music = 0
            elif percent:
                target_music = _clamp(int(percent.group(1)) / 100, 0, .6, current_music)
            elif any(w in text for w in ("тише", "убав", "сниз", "quieter", "lower")):
                target_music = max(0, current_music * .75)
            else:
                target_music = min(.6, current_music * 1.2)
            return _result("Настраиваю громкость фоновой музыки.", [{"type": "volume", "music_volume": round(target_music, 3)}])
        if any(w in text for w in ("без звука", "выключи звук", "mute", "тишина")):
            original = 0
        elif percent:
            original = _clamp(int(percent.group(1)) / 100, 0, 2, current_original)
        elif any(w in text for w in ("тише", "убав", "сниз", "quieter", "lower")):
            original = max(0, current_original * .75)
        elif any(w in text for w in ("громче", "прибав", "увелич", "louder", "raise")):
            original = min(2, current_original * 1.2)
        else:
            original = current_original
        return _result("Настраиваю громкость оригинального звука.", [{"type": "volume", "original_volume": round(original, 3)}])

    if any(w in text for w in ("обреж", "укороти", "подреж", "trim")):
        segments = context.get("segments") if isinstance(context.get("segments"), list) else []
        selected = int(_clamp(context.get("selected_segment_index"), 0, max(0, len(segments) - 1), 0))
        current = segments[selected] if segments else {}
        start, end = current.get("start", 0), current.get("end", 0)
        if len(times) >= 2:
            start, end = times[0], times[1]
        elif times and any(w in text for w in ("до", "заканч", "конец", "end")):
            end = times[0]
        elif times and any(w in text for w in ("с", "от", "начало", "start")):
            start = times[0]
        if float(end or 0) > float(start or 0):
            return _result("Подрезаю выбранный фрагмент по указанным границам исходного видео.", [{"type": "trim", "start_seconds": start, "end_seconds": end}])
        if not ops:
            return {"reply": "Укажите границы обрезки, например: «оставь выбранный фрагмент с 5 по 18 секунду». Время относится к исходному видео.", "operations": []}

    color_intent = any(w in text for w in ("цвет", "тепл", "холод", "ярк", "светле", "контраст", "насыщ", "black and white", "чёрно-бел", "черно-бел"))
    if color_intent:
        grade = dict(context.get("grade") or {"brightness": 0, "contrast": 1, "saturation": 1, "temperature": 0, "tint": 0})
        proposal = _fallback(prompt)
        candidate = proposal.get("grade") or {}
        if "тепл" in text or "warm" in text or "золот" in text:
            grade["temperature"] = candidate.get("temperature", .4)
        if "холод" in text or "cool" in text or "син" in text or "blue" in text:
            grade["temperature"] = candidate.get("temperature", -.4)
        if "ярк" in text or "bright" in text or "светле" in text:
            grade.update(brightness=candidate.get("brightness", .08), contrast=candidate.get("contrast", grade.get("contrast", 1)))
        if "контраст" in text or "contrast" in text:
            grade["contrast"] = candidate.get("contrast", 1.12)
        if "насыщ" in text or "vibrant" in text or "цвет" in text or "color" in text:
            grade["saturation"] = candidate.get("saturation", 1.08)
        if "чёрно-бел" in text or "черно-бел" in text or "black and white" in text:
            grade["saturation"] = 0
        return _result(proposal.get("note", "Цветокоррекция обновлена."), [{"type": "color", **_clean_grade(grade)}])

    if re.search(r"фон|объект|замени|добавь.*(?:человек|дерев|предмет)|background|replace|weather|lighting", text):
        return _result("Готовлю запрос для генеративной правки сцены. Она затронет выбранный фрагмент длительностью до 5 секунд.", [
            {"type": "scene", "scene_prompt": prompt[:500]}])

    if ops:
        return _result("Проверьте выполненные изменения на монтажной шкале.")

    return None


def _sanitize_plan(value, context):
    if not isinstance(value, dict):
        return {"reply": "Не удалось разобрать команду. Уточните действие и момент на шкале.", "operations": []}
    operations = []
    total = _clamp(context.get("timeline_duration"), 0, 86400, 0)
    for raw in value.get("operations", [])[:6]:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("type")
        if kind == "split":
            at = _clamp(raw.get("at_seconds"), 0, total or 86400, -1)
            if at >= 0:
                operations.append({"type": kind, "at_seconds": at})
        elif kind == "trim":
            start = _clamp(raw.get("start_seconds"), 0, 86400, -1)
            end = _clamp(raw.get("end_seconds"), 0, 86400, -1)
            if start >= 0 and end > start:
                operations.append({"type": kind, "start_seconds": start, "end_seconds": end})
        elif kind == "transition":
            style = raw.get("transition_type") if raw.get("transition_type") in TRANSITIONS else "dissolve"
            op = {"type": kind, "transition_type": style, "duration": _clamp(raw.get("duration"), .15, 2, .7)}
            after = raw.get("after_seconds")
            if after is not None:
                op["after_seconds"] = _clamp(after, 0, total or 86400, 0)
            else:
                op["target_index"] = int(_clamp(raw.get("target_index", context.get("selected_segment_index")), -1, 79, -1))
            operations.append(op)
        elif kind == "color":
            operations.append({"type": kind, **_clean_grade(raw)})
        elif kind == "volume":
            op = {"type": kind}
            if raw.get("original_volume") is not None:
                op["original_volume"] = _clamp(raw.get("original_volume"), 0, 2, 1)
            if raw.get("music_volume") is not None:
                op["music_volume"] = _clamp(raw.get("music_volume"), 0, .6, .12)
            if len(op) > 1:
                operations.append(op)
        elif kind == "duck_music":
            operations.append({"type": kind, "enabled": bool(raw.get("enabled", True))})
        elif kind == "audio_track":
            track = raw.get("audio_track")
            mode = raw.get("track_mode")
            if track in ("original", "music", "sfx") and mode in ("mute", "solo"):
                operations.append({"type": kind, "audio_track": track, "track_mode": mode,
                                   "enabled": bool(raw.get("enabled", True))})
        elif kind == "format" and raw.get("aspect") in ("source", "9:16", "1:1", "16:9"):
            operations.append({"type": kind, "aspect": raw["aspect"]})
        elif kind == "scene":
            scene = str(raw.get("scene_prompt") or "").strip()
            if scene:
                operations.append({"type": kind, "scene_prompt": scene[:500]})
        elif kind == "analyze":
            operations.append({"type": kind})
        elif kind == "captions":
            style = str(raw.get("caption_style") or "creator")
            if style in captions.STYLES:
                operations.append({"type": kind, "caption_style": style,
                                   "caption_animation": raw.get("caption_animation") if raw.get("caption_animation") in ("none", "pop", "bounce", "fade") else "none",
                                   "caption_highlight": bool(raw.get("caption_highlight", True))})
        elif kind == "sfx":
            prompt = str(raw.get("effect_prompt") or "").strip()[:400]
            if prompt:
                operations.append({"type": kind, "effect_prompt": prompt,
                                   "at_seconds": _clamp(raw.get("at_seconds"), 0, total or 86400, 0),
                                   "duration": _clamp(raw.get("duration"), .5, 12, 4)})
        elif kind == "face_blur":
            operations.append({"type": kind, "mode": raw.get("mode") if raw.get("mode") in ("gaussian", "pixelate") else "gaussian"})
        elif kind == "remove_pauses":
            operations.append({"type": kind})
        elif kind == "upscale":
            target = int(_clamp(raw.get("target_height"), 720, 2160, 1080))
            operations.append({"type": kind, "target_height": target})
        elif kind == "interpolate":
            target_fps = _clamp(raw.get("target_fps"), 24, 120, 0)
            multiplier = int(_clamp(raw.get("multiplier"), 2, 4, 2))
            if multiplier in (2, 3, 4):
                operations.append({"type": kind, "multiplier": multiplier, **({"target_fps": target_fps} if target_fps else {})})
    return {"reply": str(value.get("reply") or "Команда разобрана.").strip()[:400], "operations": operations}


async def assist(prompt: str, context: dict | None = None):
    """Turn Russian or English editing requests into bounded, executable local timeline operations."""
    text = str(prompt or "").strip()[:500]
    if not text:
        raise ValueError("Введите команду для монтажа.")
    context = context if isinstance(context, dict) else {}
    fallback = _fallback_plan(text, context)
    if fallback is not None:
        return _sanitize_plan(fallback, context)
    visual = context.get("video_evidence") if isinstance(context.get("video_evidence"), dict) else None
    video_evidence = None
    if visual:
        video_evidence = {
            "content_summary": str(visual.get("content_summary") or "")[:700],
            "visual_summary": str(visual.get("visual_summary") or "")[:700],
            "opening_note": str(visual.get("opening_note") or "")[:240],
            "ending_note": str(visual.get("ending_note") or "")[:240],
            "issues": [str(item)[:180] for item in visual.get("issues", [])[:5] if str(item).strip()]
                if isinstance(visual.get("issues"), list) else [],
            "edit_suggestions": [str(item)[:180] for item in visual.get("edit_suggestions", [])[:5] if str(item).strip()]
                if isinstance(visual.get("edit_suggestions"), list) else [],
            "frame_notes": [{"source_time_seconds": _clamp(item.get("time"), 0, 86400, 0),
                             "note": str(item.get("note") or "")[:180],
                             "confidence": item.get("confidence") if item.get("confidence") in ("low", "medium", "high") else "low"}
                            for item in visual.get("frame_notes", [])[:8] if isinstance(item, dict)]
                if isinstance(visual.get("frame_notes"), list) else [],
            "scene_change_candidates": [{"source_time_seconds": _clamp(item.get("time"), 0, 86400, 0),
                                         "difference_score": _clamp(item.get("score"), 0, 1, 0)}
                                        for item in visual.get("scene_changes", [])[:12] if isinstance(item, dict)]
                if isinstance(visual.get("scene_changes"), list) else [],
            "scene_scan_completed": bool(visual.get("scene_detection_available")),
            "basis": "prior local analysis of transcript and sampled frames; incomplete evidence",
        }
    safe_context = {
        "timeline_duration": _clamp(context.get("timeline_duration"), 0, 86400, 0),
        "playhead_seconds": _clamp(context.get("playhead_seconds"), 0, 86400, 0),
        "selected_segment_index": int(_clamp(context.get("selected_segment_index"), -1, 79, -1)),
        "segments": context.get("segments", [])[:30] if isinstance(context.get("segments", []), list) else [],
        "grade": _clean_grade(context.get("grade") or {}),
        "aspect": context.get("aspect", "source"), "original_volume": _clamp(context.get("original_volume"), 0, 2, 1),
        "music_volume": _clamp(context.get("music_volume"), 0, .6, .12),
        "duck_music_enabled": context.get("duck_music") is not False,
        "audio_mutes": {key: value is True for key, value in (context.get("audio_mutes") if isinstance(context.get("audio_mutes"), dict) else {}).items() if key in ("original", "music", "sfx")},
        "audio_solos": {key: value is True for key, value in (context.get("audio_solos") if isinstance(context.get("audio_solos"), dict) else {}).items() if key in ("original", "music", "sfx")},
        "caption_style": str(context.get("caption_style") or "creator")[:40],
        "captions_ready": bool(context.get("captions_ready")), "sfx_model_installed": bool(context.get("sfx_model_installed")),
        "face_blur_model_installed": face_blur_model_installed(),
        "video_evidence": video_evidence,
    }
    try:
        await llm.ensure()
        raw = await llm.chat([
            {"role": "system", "content": (
                "You control a professional local video-editing timeline. Interpret the user's natural-language request and return a concise Russian reply plus ordered executable operations. "
                "The context is the current project state. Timestamps are seconds from the beginning of the assembled timeline, unless the user explicitly says source time. "
                "For requests to split at the current position, use context.playhead_seconds. If no time is stated and the user does not refer to the current position, ask a brief clarification and return no operation. "
        "For 'a beautiful transition' use a tasteful dissolve of 0.7 seconds; choose fade, fadeblack, wipeleft, wiperight, slideright, slideleft, circleopen, or smoothleft only if requested. "
                "If the user asks to split and add a transition at that cut, return split first, then transition with after_seconds equal to the split position. "
                "For color edits, return absolute values preserving every setting not mentioned, within brightness -0.25..0.25, contrast 0.5..1.8, saturation 0..1.8, temperature/tint -1..1. "
                "When video_evidence is present, it is a summary from transcript and sampled frames, not a full frame-by-frame inspection. Use it only for supported, reversible choices; do not invent visual facts or claim to have seen the whole video. "
                "Sampled-frame-note timestamps and scene-change candidates in video_evidence are source seconds for the analyzed source, not assembled timeline seconds. For a cut request based on a detected shot, only use a matching current segment; convert source time to timeline time using that segment's source/start and timeline_start. If there are repeated source ranges or no matching evidence, ask instead of guessing. A detector score is only a visual-difference cue, not proof that a cut improves the story. "
                "Context reports whether captions, the local SFX model, and the local face detector are ready; do not claim features are ready when their model is absent. Creation and selection of new Shorts belongs to the dedicated Video · Clips page: for requests to find, create, or cut new Shorts/clips, return no operations and direct the user there. Editing existing clips on the timeline remains in this editor. Supported operation types: split, trim, transition, color, volume, duck_music, audio_track, format, scene, analyze, captions, sfx, face_blur, remove_pauses, upscale, interpolate. For audio_track use audio_track=original, music, or sfx; track_mode=mute or solo; enabled=false clears that mode. Respect existing audio_mutes and audio_solos unless the user changes them. Solo means hear only the soloed unmuted audio tracks; mute takes priority. For an explicit request to enable or disable music ducking under speech, return duck_music with enabled true or false; leave the music level unchanged. For a face-blur request, return face_blur; if the model is not installed, explain that the user can install YuNet in the editor. For remove pauses return remove_pauses; it groups speech from local word timestamps and only cuts long gaps. For upscale return target_height 1080 unless another resolution is requested. For 60 FPS return interpolate target_fps 60. For a caption-style request, return captions with one of minimal, bold, creator, movie, subtitle, opus, podcast, clean, reels, neon, cinematic, boxed, outline; set caption_animation to none, pop, bounce, or fade. For an explicit request to create a sound effect, return sfx with a concise effect_prompt grounded in the request and at_seconds equal to the current playhead unless another point is explicit; do not generate music or speech unless requested. A scene operation should only be used for an explicit visual generation request, and scene_prompt must preserve the requested meaning. "
                "Do not claim an operation was completed unless it is included. If the project lacks enough context, ask instead of guessing. Do not invent video content, dialogue, captions, or facts. Return only JSON matching the schema."
            )},
            {"role": "user", "content": "PROJECT CONTEXT:\n" + json.dumps(safe_context, ensure_ascii=False) + "\n\nEDIT REQUEST:\n" + text},
        ], schema=EDIT_SCHEMA, max_tokens=900, temperature=0.1)
        return _sanitize_plan(llm._json(raw), context)
    except Exception as exc:
        log.info("Local edit agent unavailable; using conservative command parsing: %s", exc)
        return {"reply": "Локальная модель недоступна. " + (fallback or {}).get("reply", "Уточните команду или установите модель Gemma."),
                "operations": (fallback or {}).get("operations", [])}


SA3_ROOT = ROOT / "apps" / "stable-audio-3" / "optimized" / "tflite"
FACE_BLUR_ROOT = ROOT / "envs" / "env-face-blur"
FACE_BLUR_PYTHON = FACE_BLUR_ROOT / "Scripts" / "python.exe"
FACE_BLUR_SCRIPT = ROOT / "workers" / "face_blur.py"
FACE_MODEL = MODELS / "vision" / "face_detection_yunet_2023mar.onnx"
FACE_MODEL_URL = "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
FACE_MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
SAM2_ROOT = MODELS / "vision" / "sam2.1-hiera-tiny"
SAM2_PYTHON = ROOT / "envs" / "env-comfyui" / "Scripts" / "python.exe"
SAM2_WORKER = ROOT / "workers" / "sam2_video_track.py"
SUBJECT_TRACK_WORKER = ROOT / "workers" / "subject_track.py"
SAM2_SAMPLE_FPS = 8
SA3_SCRIPT = SA3_ROOT / "scripts" / "sa3_tflite.py"
SA3_VENV_PYTHON = SA3_ROOT / ".venv" / "Scripts" / "python.exe"
SA3_WEIGHTS = [
    SA3_ROOT / "models" / "tflite" / "sa3-sm-sfx" / "dit_fp32.tflite",
    SA3_ROOT / "models" / "tflite" / "t5gemma" / "encoder_fp16.tflite",
    SA3_ROOT / "models" / "tflite" / "same-s" / "enc_w8a8.tflite",
    SA3_ROOT / "models" / "tflite" / "same-s" / "dec_w8a8.tflite",
]


def sfx_model_installed() -> bool:
    return SA3_VENV_PYTHON.is_file() and SA3_SCRIPT.is_file() and all(p.is_file() and p.stat().st_size > 0 for p in SA3_WEIGHTS)


def face_blur_model_installed() -> bool:
    return FACE_BLUR_PYTHON.is_file() and FACE_BLUR_SCRIPT.is_file() and FACE_MODEL.is_file() and FACE_MODEL.stat().st_size > 100_000


def sam2_model_installed() -> bool:
    required = ("config.json", "model.safetensors", "preprocessor_config.json",
                "processor_config.json", "video_preprocessor_config.json")
    return SAM2_PYTHON.is_file() and SAM2_WORKER.is_file() and all(
        (SAM2_ROOT / name).is_file() and (SAM2_ROOT / name).stat().st_size > 0 for name in required)


async def _run_external(job, command, cwd: Path, message: str, start: float, span: float, env=None):
    """Run an installer/model CLI with job progress and a useful tail on failure."""
    job.message, job.progress = message, start
    proc = await asyncio.create_subprocess_exec(*map(str, command), cwd=str(cwd), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    tail = []
    while True:
        if job.cancel_requested:
            proc.terminate()
            await proc.wait()
            raise RuntimeError("Generation was cancelled.")
        try:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        if not raw:
            if proc.returncode is not None:
                break
            continue
        line = raw.decode("utf-8", errors="replace").strip("\r\n ")
        if not line:
            continue
        tail.append(line)
        tail = tail[-8:]
        job.message = line[-180:]
        match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)%", line)
        if match:
            job.progress = min(start + span, start + span * float(match.group(1)) / 100)
    code = await proc.wait()
    if code:
        raise RuntimeError("\n".join(tail)[-1200:] or f"Local model process exited with code {code}.")


async def run_sfx_install(job):
    """Install the isolated LiteRT runtime and official Stable Audio 3 Small-SFX weights."""
    if sfx_model_installed():
        job.result = {"installed": True}
        job.message = "Модель Stable Audio 3 Small-SFX уже установлена."
        return
    if not SA3_SCRIPT.is_file():
        raise ValueError("Не найден официальный локальный модуль Stable Audio 3. Повторно запустите установку LocalAI.")
    base_python = Path(getattr(sys, "_base_executable", sys.executable))
    venv_dir = SA3_ROOT / ".venv"
    if not SA3_VENV_PYTHON.is_file():
        await _run_external(job, [base_python, "-m", "venv", venv_dir], SA3_ROOT,
                            "Создаю отдельную среду для генерации звуков…", .02, .08)
    await _run_external(job, [SA3_VENV_PYTHON, "-m", "pip", "install", "-r", SA3_ROOT / "requirements.txt"],
                        SA3_ROOT, "Устанавливаю зависимости Stable Audio…", .10, .12)
    env = os.environ.copy()
    env.update({"HF_HOME": str(ROOT / "cache" / "hf"), "HF_HUB_DISABLE_TELEMETRY": "1",
                "PYTHONIOENCODING": "utf-8", "INSTALL_SKIP_PIP": "1"})
    env.pop("HF_HUB_OFFLINE", None)
    env.pop("TRANSFORMERS_OFFLINE", None)
    await _run_external(job, [SA3_VENV_PYTHON, SA3_ROOT / "scripts" / "install.py", "--download", "sm-sfx"],
                        SA3_ROOT, "Загружаю веса Stable Audio 3 Small-SFX (около 2,6 ГБ)…", .22, .77, env)
    if not sfx_model_installed():
        raise RuntimeError("Загрузка завершилась, но один или несколько файлов модели отсутствуют.")
    job.progress, job.message = .99, "Модель Stable Audio 3 Small-SFX установлена."
    job.result = {"installed": True, "weights_gb": round(sum(p.stat().st_size for p in SA3_WEIGHTS) / 1e9, 2)}


async def run_face_blur_install(job):
    """Install OpenCV in an isolated CPU environment and fetch official YuNet weights from HF."""
    if face_blur_model_installed():
        job.result = {"installed": True}
        job.message = "YuNet для локального размытия лиц уже установлен."
        return
    FACE_BLUR_ROOT.mkdir(parents=True, exist_ok=True)
    FACE_MODEL.parent.mkdir(parents=True, exist_ok=True)
    base_python = Path(getattr(sys, "_base_executable", sys.executable))
    if not FACE_BLUR_PYTHON.is_file():
        await _run_external(job, [base_python, "-m", "venv", FACE_BLUR_ROOT], FACE_BLUR_ROOT,
                            "Создаю изолированную среду для детектора лиц…", .02, .10)
    await _run_external(job, [FACE_BLUR_PYTHON, "-m", "pip", "install", "--disable-pip-version-check", "opencv-python-headless"],
                        FACE_BLUR_ROOT, "Устанавливаю OpenCV для локальной обработки на CPU…", .12, .46)

    def download_verified():
        import hashlib
        import urllib.request
        temp_path = FACE_MODEL.with_suffix(".onnx.download")
        request = urllib.request.Request(FACE_MODEL_URL, headers={"User-Agent": "LocalAI-Studio/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response, temp_path.open("wb") as output:
            shutil.copyfileobj(response, output)
        digest = hashlib.sha256(temp_path.read_bytes()).hexdigest()
        if digest != FACE_MODEL_SHA256:
            temp_path.unlink(missing_ok=True)
            raise RuntimeError("Хеш файла YuNet не совпал с проверенной версией OpenCV Zoo.")
        temp_path.replace(FACE_MODEL)

    job.message, job.progress = "Загружаю YuNet с Hugging Face и проверяю файл…", .62
    await asyncio.to_thread(download_verified)
    if not face_blur_model_installed():
        raise RuntimeError("Установка YuNet завершилась неполно: модель или OpenCV не найдены.")
    job.progress, job.message = .99, "Юнет установлен; размытие лиц готово к запуску."
    job.result = {"installed": True, "model": "opencv/opencv_zoo · YuNet", "model_bytes": FACE_MODEL.stat().st_size}


async def run_model_install(job):
    model_id = str(job.params.get("model_id") or "")
    if model_id == "stable_audio_3_sfx":
        await run_sfx_install(job)
    elif model_id == "yunet_face_blur":
        await run_face_blur_install(job)
    elif model_id == "sam2_video_mask":
        await run_sam2_install(job)
    else:
        raise ValueError("Для этой модели автоматическая установка не настроена.")


async def run_sam2_install(job):
    if sam2_model_installed():
        job.result = {"installed": True, "model": "facebook/sam2.1-hiera-tiny"}
        job.message = "SAM 2.1 Hiera Tiny уже установлена."
        return
    SAM2_ROOT.mkdir(parents=True, exist_ok=True)
    if not SAM2_PYTHON.is_file() or not SAM2_WORKER.is_file():
        raise ValueError("Не найдена среда Transformers LocalAI для SAM 2.")
    env = os.environ.copy()
    env.update({"HF_HOME": str(ROOT / "cache" / "hf"), "HF_HUB_DISABLE_TELEMETRY": "1",
                "PYTHONIOENCODING": "utf-8"})
    env.pop("HF_HUB_OFFLINE", None)
    env.pop("TRANSFORMERS_OFFLINE", None)
    await _run_external(job, [SAM2_PYTHON, SAM2_WORKER, "--install", "--model-dir", SAM2_ROOT], ROOT,
                        "Загружаю официальные веса SAM 2.1 Hiera Tiny с Hugging Face…", .03, .92, env)
    if not sam2_model_installed():
        raise RuntimeError("Загрузка SAM 2 завершилась неполно: необходимые файлы модели не найдены.")
    job.progress, job.message = .99, "SAM 2 установлена. Маски можно отслеживать по видео."
    job.result = {"installed": True, "model": "facebook/sam2.1-hiera-tiny",
                  "size_mb": round((SAM2_ROOT / "model.safetensors").stat().st_size / 1e6, 1),
                  "license": "Apache-2.0"}


async def run_video_mask_track(job):
    """Track a point-selected object with SAM 2, then blur or pixelate only that mask."""
    if not sam2_model_installed():
        raise ValueError("Сначала установите SAM 2 в панели AI‑монтажёра.")
    p = job.params
    source = jobs.resolve_input(p.get("video"))
    info = media.probe(source)
    if not info.get("has_video") or info.get("duration", 0) <= 0:
        raise ValueError("Не удалось прочитать исходное видео.")
    start = _clamp(p.get("start"), 0, info["duration"], -1)
    end = _clamp(p.get("end"), 0, info["duration"], -1)
    seed_time = _clamp(p.get("seed_time"), start, end, -1)
    point = p.get("point") if isinstance(p.get("point"), dict) else {}
    x, y = _clamp(point.get("x"), 0, 1, -1), _clamp(point.get("y"), 0, 1, -1)
    if start < 0 or end <= start + .5 or end - start > 60:
        raise ValueError("Выберите диапазон от 0,5 до 60 секунд для отслеживания маски.")
    if seed_time < start or seed_time > end or x < 0 or y < 0:
        raise ValueError("На предпросмотре выберите точку внутри объекта и временного диапазона.")
    mode = str(p.get("mode") or "gaussian")
    if mode not in ("gaussian", "pixelate"):
        mode = "gaussian"
    sample_fps = SAM2_SAMPLE_FPS
    job.message = "Подготавливаю последовательный запуск SAM 2 на GPU…"
    await resources.prepare("comfyui", "tool:sam2", notify=lambda m: setattr(job, "message", m))
    work = WORK / (job.id + "-sam2")
    work.mkdir(parents=True, exist_ok=True)
    masks = work / "masks"
    silent = work / "tracked-mask.mp4"
    destination = jobs.next_output_path(OUT_VIDEO_EDIT, "trackedmask", "mp4")
    began = time.time()
    try:
        env = os.environ.copy()
        env.update({"HF_HOME": str(ROOT / "cache" / "hf"), "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "PYTHONIOENCODING": "utf-8"})
        await _run_external(job, [SAM2_PYTHON, SAM2_WORKER, "--video", source, "--model-dir", SAM2_ROOT,
                                  "--masks", masks, "--ffmpeg", media.FFMPEG, "--start", start, "--end", end,
                                  "--seed-time", seed_time, "--x", x, "--y", y,
                                  "--duration", info["duration"], "--sample-fps", sample_fps], ROOT,
                            "SAM 2 выделяет объект и отслеживает маску в обе стороны…", .05, .69, env)
        if not any(masks.glob("*.png")):
            raise RuntimeError("SAM 2 не создала маску для выбранного фрагмента.")
        job.message, job.progress = "Размываю выбранный объект и сохраняю исходный звук…", .78
        if mode == "pixelate":
            effect = f"scale={info['width']//24}:{info['height']//24}:flags=neighbor,scale={info['width']}:{info['height']}:flags=neighbor"
        else:
            effect = "boxblur=luma_radius=24:luma_power=2:chroma_radius=20:chroma_power=2"
        graph = (f"[0:v]split=2[base][effectsrc];[effectsrc]{effect}[effect];"
                 f"[1:v]fps={info['fps']:.3f},scale={info['width']}:{info['height']},format=gray,gblur=sigma=1.3[mask];"
                 "[base][effect][mask]maskedmerge=planes=7[vout]")
        media.run(["-i", source, "-framerate", str(sample_fps), "-start_number", "0", "-i", masks / "%08d.png",
                   "-filter_complex", graph, "-map", "[vout]", "-map", "0:a:0?", "-t", info["duration"],
                   "-r", f"{info['fps']:.3f}", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                   "-movflags", "+faststart", silent], timeout=7200)
        # The intermediate and final paths are split so a failed encode cannot replace the source.
        silent.replace(destination)
        output_info = media.probe(destination)
        rel = jobs.rel(destination)
        job.gen_id = db.add_generation(type="video", engine="Transformers · CUDA", model="SAM 2.1 Hiera Tiny",
                                       prompt="Track the selected object and apply a local privacy mask",
                                       params={"mode": mode, "start": start, "end": end, "seed_time": seed_time,
                                               "sample_fps": sample_fps, "device": "CUDA"},
                                       inputs={"video": jobs.rel(source)}, outputs=[rel], status="done",
                                       seconds=round(time.time() - began, 1))
        job.progress, job.message = .98, "Маска отслежена. Проверьте края и быстрые движения перед экспортом."
        job.result = {"outputs": [rel], "duration": round(output_info["duration"], 2),
                      "width": output_info["width"], "height": output_info["height"],
                      "start": start, "end": end, "seed_time": seed_time, "sample_fps": sample_fps,
                      "mode": mode, "device": "CUDA"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        await resources.after_job("comfyui")


async def run_video_face_blur(job):
    """Detect faces locally with YuNet and blur each detected face, preserving source audio."""
    if not face_blur_model_installed():
        raise ValueError("Сначала установите YuNet во вкладке «AI‑монтажёр».")
    source = jobs.resolve_input(job.params.get("video"))
    info = media.probe(source)
    if not info.get("has_video") or info["duration"] <= 0:
        raise ValueError("Не удалось прочитать выбранное видео.")
    destination = jobs.next_output_path(OUT_VIDEO_EDIT, "faceblur", "mp4")
    silent = WORK / (job.id + "-faceblur.mp4")
    silent.parent.mkdir(parents=True, exist_ok=True)
    mode = str(job.params.get("mode") or "gaussian")
    if mode not in ("gaussian", "pixelate"):
        mode = "gaussian"
    command = [FACE_BLUR_PYTHON, FACE_BLUR_SCRIPT, "--input", source, "--output", silent,
               "--model", FACE_MODEL, "--ffmpeg", media.FFMPEG, "--mode", mode]
    started = time.time()
    await _run_external(job, command, ROOT, "Обнаруживаю и размываю лица по кадрам…", .05, .82)
    if not silent.is_file() or silent.stat().st_size < 4096:
        raise RuntimeError("Обработка YuNet завершилась без видеофайла.")
    job.message, job.progress = "Возвращаю исходную звуковую дорожку…", .90
    media.run(["-i", silent, "-i", source, "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy",
               "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", destination], timeout=1800)
    silent.unlink(missing_ok=True)
    output_info = media.probe(destination)
    rel = jobs.rel(destination)
    job.gen_id = db.add_generation(type="video", engine="OpenCV DNN", model="YuNet face blur",
                                   prompt="Blur detected faces in the selected source", params={"mode": mode, "device": "CPU"},
                                   inputs={"video": jobs.rel(source)}, outputs=[rel], status="done",
                                   seconds=round(time.time() - started, 1))
    job.progress, job.message = .98, "Размытие лиц готово. Проверьте результат перед публикацией."
    job.result = {"outputs": [rel], "duration": round(output_info["duration"], 2),
                  "width": output_info["width"], "height": output_info["height"], "faces_processed": True}


async def run_sfx_generation(job):
    p = dict(job.params)
    if not sfx_model_installed():
        raise ValueError("Сначала установите Stable Audio 3 Small-SFX во вкладке «Звук».")
    prompt = str(p.get("prompt") or "").strip()[:400]
    if not prompt:
        raise ValueError("Опишите нужный звуковой эффект.")
    duration = _clamp(p.get("duration"), .5, 12, 4)
    model_prompt = prompt
    if re.search(r"[А-Яа-яЁё]", prompt):
        try:
            await llm.ensure()
            model_prompt = await llm.chat([
                {"role": "system", "content": "Translate the user's sound-effect idea into one concise English acoustic prompt for a sound-effects generator. Preserve the exact intended sound. Do not add music, speech, narration, religious sounds, brands, or extra events unless the user explicitly requested them. Return only the prompt."},
                {"role": "user", "content": prompt},
            ], max_tokens=120, temperature=0.1)
            model_prompt = str(model_prompt).strip().strip('"')[:400] or prompt
        except Exception as exc:
            log.warning("SFX prompt translation unavailable, passing original text: %s", exc)
        finally:
            await resources.after_job("llm")
    output = jobs.next_output_path(OUT_AUDIO, "sfx", "wav")
    started = time.time()
    env = os.environ.copy()
    env.update({"HF_HOME": str(ROOT / "cache" / "hf"), "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "PYTHONIOENCODING": "utf-8"})
    await _run_external(job, [SA3_VENV_PYTHON, SA3_SCRIPT, "--prompt", model_prompt, "--dit", "sm-sfx",
                              "--decoder", "same-s", "--seconds", str(duration), "--out", output, "--threads", "8"],
                        SA3_ROOT, "Генерирую звуковой эффект на компьютере…", .05, .90, env)
    if not output.is_file() or output.stat().st_size < 2048:
        raise RuntimeError("Stable Audio завершился без готового WAV-файла.")
    rel = jobs.rel(output)
    job.gen_id = db.add_generation(type="audio", engine="Stable Audio 3 TFLite", model="Stable Audio 3 Small-SFX",
                                   prompt=prompt, params={"model_prompt": model_prompt, "duration": duration,
                                                          "sample_rate": 44100, "device": "CPU"},
                                   inputs={}, outputs=[rel], status="done", seconds=round(time.time() - started, 1))
    job.progress, job.message = .98, "SFX готов. Добавляю его в проект…"
    job.result = {"outputs": [rel], "duration": duration, "prompt": prompt}


async def run_timeline_transcription(job):
    """Transcribe source files once and return word timings for timeline caption placement."""
    raw_sources = job.params.get("sources") or []
    if not raw_sources or len(raw_sources) > 20:
        raise ValueError("Добавьте до 20 исходных видео для автоматической расшифровки.")
    work = WORK / ("transcribe-" + job.id)
    work.mkdir(parents=True, exist_ok=True)
    import dubbing
    await resources.prepare("media", "media:editor-captions", notify=lambda m: setattr(job, "message", m))
    results = {}
    try:
        for index, source in enumerate(raw_sources):
            src = jobs.resolve_input(source.get("path") if isinstance(source, dict) else source)
            source_id = str(source.get("id", index)) if isinstance(source, dict) else str(index)
            info = media.probe(src)
            if not info.get("has_audio"):
                results[source_id] = {"language": None, "words": []}
                continue
            job.message = f"Whisper расшифровывает исходник {index + 1}/{len(raw_sources)}…"
            job.progress = .05 + .8 * index / max(1, len(raw_sources))
            audio = work / f"source_{index:02d}.wav"
            media.run(["-i", src, "-vn", "-ac", "1", "-ar", "16000", audio], timeout=7200)
            transcript = await dubbing.media_call("/transcribe", {"path": str(audio)}, timeout=7200)
            words = [word for segment in transcript.get("segments", []) for word in segment.get("words", [])
                     if word.get("word") and float(word.get("end", 0)) > float(word.get("start", 0))]
            results[source_id] = {"language": transcript.get("language"), "words": words}
        if not any(result["words"] for result in results.values()):
            raise ValueError("В выбранных исходниках не найдена речь для субтитров.")
    finally:
        await resources.after_job("media")
        shutil.rmtree(work, ignore_errors=True)
    job.progress, job.message = .98, "Расшифровка готова."
    job.result = {"captions": results}


async def run_timeline_render(job):
    p = dict(job.params)
    raw_sources = p.get("sources") or []
    segments = p.get("segments") or []
    if not raw_sources or not segments:
        raise ValueError("Добавьте исходное видео и хотя бы один фрагмент на таймлайн.")
    if len(raw_sources) > 20 or len(segments) > 80:
        raise ValueError("Поддерживается до 20 исходников и 80 фрагментов в одном проекте.")

    sources, info = [], []
    for item in raw_sources:
        path = jobs.resolve_input(item.get("path") if isinstance(item, dict) else item)
        if path.suffix.lower() not in (".mp4", ".mov", ".webm", ".mkv"):
            raise ValueError("Таймлайн принимает видеофайлы MP4, MOV, WebM и MKV.")
        sources.append(path)
        info.append(media.probe(path))
    lookup = {str(item.get("id")): i for i, item in enumerate(raw_sources) if isinstance(item, dict)}
    normalized = []
    for seg in segments:
        source_idx = lookup.get(str(seg.get("source"))) if isinstance(seg, dict) else None
        if source_idx is None and len(sources) == 1:
            source_idx = 0
        if source_idx is None or source_idx >= len(sources):
            raise ValueError("У одного из фрагментов не найден исходный видеофайл.")
        start = _clamp(seg.get("start"), 0, info[source_idx]["duration"], -1)
        end = _clamp(seg.get("end"), 0, info[source_idx]["duration"], -1)
        if start < 0 or end <= start + 0.04:
            raise ValueError("Проверьте начало и конец каждого фрагмента.")
        raw_transition = seg.get("transition_after") if isinstance(seg, dict) else None
        transition = None
        if isinstance(raw_transition, dict) and raw_transition.get("type") in TRANSITIONS:
            transition = {"type": raw_transition["type"], "duration": _clamp(raw_transition.get("duration"), .15, 2, .7)}
        normalized.append((source_idx, start, end, transition))

    grade = _clean_grade(p.get("grade") or {})
    aspect = p.get("aspect", "source")
    base = info[normalized[0][0]]
    if aspect == "9:16":
        width, height = 1080, 1920
    elif aspect == "1:1":
        width, height = 1080, 1080
    elif aspect == "16:9":
        width, height = 1920, 1080
    else:
        width, height = base["width"] // 2 * 2, base["height"] // 2 * 2
    if width * height > 3840 * 2160:
        raise ValueError("Разрешение исходного видео выше 4K и не поддерживается для экспорта.")

    subject_follow = bool(p.get("subject_follow", False))
    target_aspect = width / max(1, height)
    subject_tracks, subject_crop_files, track_work = {}, {}, None
    reframe_report = {"requested": subject_follow, "target_aspect": round(target_aspect, 4),
                      "tracked_sources": 0, "fallback_sources": 0, "tracked_samples": 0,
                      "source_tracks": {}}
    eligible_sources = [index for index, source_info in enumerate(info)
                        if source_info["height"] > 0 and source_info["width"] / source_info["height"] > target_aspect + .025]
    if subject_follow and eligible_sources:
        if not face_blur_model_installed():
            reframe_report["fallback_sources"] = len(eligible_sources)
            reframe_report["reason"] = "YuNet не установлен; использовано центральное кадрирование."
        else:
            track_work = WORK / (job.id + "-subject")
            track_work.mkdir(parents=True, exist_ok=True)
            for order, source_idx in enumerate(eligible_sources):
                track_file = track_work / f"source_{source_idx:02d}.json"
                crop_file = track_work / f"source_{source_idx:02d}_reframed.mp4"
                job.message = f"Отслеживаю положение говорящего для кадрирования ({order + 1}/{len(eligible_sources)})…"
                job.progress = .03 + .08 * order / max(1, len(eligible_sources))
                try:
                    await _run_external(job, [FACE_BLUR_PYTHON, SUBJECT_TRACK_WORKER,
                                              "--video", sources[source_idx], "--model", FACE_MODEL,
                                              "--output", track_file, "--crop-output", crop_file,
                                              "--ffmpeg", media.FFMPEG, "--crop-width", width,
                                              "--crop-height", height, "--sample-fps", 1.0], ROOT,
                                        "Анализирую положение говорящего на CPU…",
                                        .03 + .08 * order / max(1, len(eligible_sources)), .08 / max(1, len(eligible_sources)))
                    track = json.loads(track_file.read_text(encoding="utf-8"))
                    track_file.unlink(missing_ok=True)
                    if track.get("detected") and track.get("points") and track.get("crop_available") and crop_file.is_file():
                        subject_tracks[source_idx] = track
                        subject_crop_files[source_idx] = crop_file
                        reframe_report["tracked_sources"] += 1
                        reframe_report["tracked_samples"] += len(track["points"])
                        source_item = raw_sources[source_idx]
                        source_id = str(source_item.get("id", source_idx)) if isinstance(source_item, dict) else str(source_idx)
                        reframe_report["source_tracks"][source_id] = track["points"]
                    else:
                        crop_file.unlink(missing_ok=True)
                        reframe_report["fallback_sources"] += 1
                except Exception as exc:
                    track_file.unlink(missing_ok=True)
                    crop_file.unlink(missing_ok=True)
                    log.warning("Subject-follow crop unavailable for source %s: %s", source_idx, exc)
                    reframe_report["fallback_sources"] += 1
            if reframe_report["fallback_sources"]:
                reframe_report["reason"] = "Для части исходников лицо не найдено; там сохранено центральное кадрирование."
    elif subject_follow:
        reframe_report["reason"] = "Исходники не требуют горизонтального смещения для выбранного формата."
    fps_raw = p.get("fps", "source")
    fps = base["fps"] if fps_raw == "source" else _clamp(fps_raw, 24, 60, 30)
    fps = min(60, max(12, fps))

    brightness = grade["brightness"]
    contrast = grade["contrast"]
    saturation = grade["saturation"]
    temp = grade["temperature"] * 0.16
    tint = grade["tint"] * 0.12
    eq = (f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f},"
          f"colorbalance=rs={temp:.4f}:gs={tint:.4f}:bs={-temp:.4f}:rm={temp:.4f}:gm={tint:.4f}:bm={-temp:.4f}")
    args, graph, vlabels, alabels = [], [], [], []
    for src in sources:
        args += ["-i", str(src)]
    next_input_idx = len(sources)
    subject_video_inputs = {}
    for source_idx, crop_file in subject_crop_files.items():
        subject_video_inputs[source_idx] = next_input_idx
        args += ["-i", str(crop_file)]
        next_input_idx += 1
    duration = sum(end - start for _, start, end, _ in normalized)
    transition_plan = {}
    transition_report = []
    planned_duration = normalized[0][2] - normalized[0][1]
    for i in range(1, len(normalized)):
        _, _, end, transition = normalized[i - 1]
        _, start, next_end, _ = normalized[i]
        clip_duration = next_end - start
        requested = transition or {}
        transition_duration = min(requested.get("duration", 0), planned_duration * .45, clip_duration * .45)
        if transition_duration >= .15:
            transition_plan[i] = transition_duration
            transition_report.append({"after_segment": i, "type": requested["type"],
                                      "at": round(planned_duration - transition_duration, 3),
                                      "duration": round(transition_duration, 3)})
            planned_duration += clip_duration - transition_duration
        else:
            planned_duration += clip_duration
    for k, (source_idx, start, end, _) in enumerate(normalized):
        video_input_idx = subject_video_inputs.get(source_idx, source_idx)
        if source_idx in subject_video_inputs:
            framing = f"scale={width}:{height},setsar=1"
        else:
            framing = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
        graph.append(f"[{video_input_idx}:v]trim=start={start:.4f}:end={end:.4f},setpts=PTS-STARTPTS,fps={fps:.3f},settb=AVTB,{framing},{eq}[v{k}]")
        vlabels.append(f"[v{k}]")
        if info[source_idx]["has_audio"]:
            graph.append(f"[{source_idx}:a]atrim=start={start:.4f}:end={end:.4f},asetpts=PTS-STARTPTS,"
                         f"aformat=sample_rates=48000:channel_layouts=stereo,volume={_clamp(p.get('volume'), 0, 2, 1):.3f}[a{k}]")
        else:
            silence_idx = next_input_idx
            next_input_idx += 1
            args += ["-f", "lavfi", "-t", f"{end - start:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
            graph.append(f"[{silence_idx}:a]atrim=duration={end-start:.4f},asetpts=PTS-STARTPTS[a{k}]")
        alabels.append(f"[a{k}]")
    if len(vlabels) == 1:
        video_label, audio_label = vlabels[0].strip("[]"), alabels[0].strip("[]")
    else:
        video_label, audio_label = vlabels[0].strip("[]"), alabels[0].strip("[]")
        accumulated_duration = normalized[0][2] - normalized[0][1]
        for i in range(1, len(normalized)):
            source_idx, start, end, _ = normalized[i]
            clip_duration = end - start
            transition = normalized[i - 1][3] or {}
            transition_duration = transition_plan.get(i, 0.0)
            next_v, next_a = f"vseq{i}", f"aseq{i}"
            if transition_duration >= .15:
                graph.append(f"[{video_label}]{vlabels[i]}xfade=transition={transition['type']}:duration={transition_duration:.3f}:offset={max(0, accumulated_duration-transition_duration):.3f}[{next_v}]")
                graph.append(f"[{audio_label}]{alabels[i]}acrossfade=d={transition_duration:.3f}:c1=tri:c2=tri[{next_a}]")
                accumulated_duration += clip_duration - transition_duration
            else:
                graph.append(f"[{video_label}]{vlabels[i]}concat=n=2:v=1:a=0[{next_v}]")
                graph.append(f"[{audio_label}]{alabels[i]}concat=n=2:v=0:a=1[{next_a}]")
                accumulated_duration += clip_duration
            video_label, audio_label = next_v, next_a
        duration = accumulated_duration
    graph.append(f"[{video_label}]null[vjoined]")
    graph.append(f"[{audio_label}]anull[ajoined]")
    video_label = "vjoined"
    raw_broll = p.get("broll") if isinstance(p.get("broll"), list) else []
    for i, item in enumerate(raw_broll[:20]):
        if not isinstance(item, dict) or not item.get("path"):
            continue
        broll_path = jobs.resolve_input(item["path"])
        broll_info = media.probe(broll_path)
        source_start = _clamp(item.get("start"), 0, broll_info["duration"], 0)
        source_end = _clamp(item.get("end"), source_start, broll_info["duration"], broll_info["duration"])
        broll_at = _clamp(item.get("at"), 0, duration, 0)
        broll_length = min(source_end - source_start, max(0, duration - broll_at))
        if broll_length < .08:
            continue
        scale = _clamp(item.get("scale"), .2, .7, .38)
        overlay_width = max(2, int(width * scale) // 2 * 2)
        overlay_height = max(2, int(height * scale) // 2 * 2)
        input_idx = next_input_idx
        next_input_idx += 1
        args += ["-i", str(broll_path)]
        graph.append(f"[{input_idx}:v]trim=start={source_start:.4f}:end={source_start+broll_length:.4f},"
                     f"setpts=PTS-STARTPTS+{broll_at:.4f}/TB,fps={fps:.3f},"
                     f"scale={overlay_width}:{overlay_height}:force_original_aspect_ratio=decrease,setsar=1[broll{i}]")
        next_video = f"vwithbroll{i}"
        broll_safe_x, broll_safe_y = max(24, int(width * 0.07)), max(24, int(height * 0.12))
        graph.append(f"[{video_label}][broll{i}]overlay=x=W-w-{broll_safe_x}:y={broll_safe_y}:eof_action=pass:repeatlast=0:shortest=0:"
                     f"enable='between(t,{broll_at:.4f},{broll_at+broll_length:.4f})'[{next_video}]")
        video_label = next_video
    music = p.get("music")
    audio_label = "ajoined"
    if music:
        music_path = jobs.resolve_input(music)
        music_idx = next_input_idx
        args += ["-stream_loop", "-1", "-i", str(music_path)]
        next_input_idx += 1
        graph.append(f"[{music_idx}:a]atrim=duration={duration:.4f},asetpts=PTS-STARTPTS,"
                     f"aformat=sample_rates=48000:channel_layouts=stereo,volume={_clamp(p.get('music_volume'), 0, 0.6, 0.12):.3f}[music]")
        if p.get("duck_music") is not False:
            graph.append("[music][ajoined]sidechaincompress=threshold=0.10:ratio=3.2:attack=35:release=520:knee=4:detection=rms[duckedmusic]")
            graph.append("[ajoined][duckedmusic]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95:attack=5:release=50:level=false[amix]")
        else:
            graph.append("[ajoined][music]amix=inputs=2:duration=first:dropout_transition=0[amix]")
        audio_label = "amix"
    raw_sfx = p.get("sfx") if isinstance(p.get("sfx"), list) else []
    sfx_labels = []
    sfx_audio_expected = False
    for i, item in enumerate(raw_sfx[:24]):
        if not isinstance(item, dict) or not item.get("path"):
            continue
        sound_path = jobs.resolve_input(item["path"])
        sound_info = media.probe(sound_path)
        if not sound_info.get("has_audio"):
            continue
        sound_duration = min(30.0, max(.1, float(sound_info["duration"])))
        sound_start = _clamp(item.get("at"), 0, duration, 0)
        gain = _clamp(item.get("volume"), 0, 1.5, .25)
        input_idx = next_input_idx
        next_input_idx += 1
        args += ["-i", str(sound_path)]
        delay_ms = int(round(sound_start * 1000))
        fade_out_start = max(.05, sound_duration - .08)
        sfx_audio_expected = sfx_audio_expected or gain > 0
        graph.append(f"[{input_idx}:a]atrim=duration={sound_duration:.4f},asetpts=PTS-STARTPTS,"
                     f"aformat=sample_rates=48000:channel_layouts=stereo,volume={gain:.3f},"
                     f"afade=t=in:d=0.03,afade=t=out:st={fade_out_start:.4f}:d=0.08,"
                     f"adelay={delay_ms}|{delay_ms}[sfx{i}]")
        sfx_labels.append(f"[sfx{i}]")
    if sfx_labels:
        graph.append(f"[{audio_label}]" + "".join(sfx_labels) +
                     f"amix=inputs={len(sfx_labels) + 1}:duration=first:dropout_transition=0:normalize=0[asfxmix]")
        audio_label = "asfxmix"
    original_audio_expected = (_clamp(p.get("volume"), 0, 2, 1) > 0
                               and any(info[source_idx].get("has_audio") for source_idx, _, _, _ in normalized))
    music_audio_expected = bool(music) and _clamp(p.get("music_volume"), 0, .6, .12) > 0
    continuous_audio_expected = original_audio_expected or music_audio_expected
    audio_expectation = {"expected": continuous_audio_expected or sfx_audio_expected,
                         "continuous": continuous_audio_expected, "transient": sfx_audio_expected}
    if bool(p.get("normalize_audio", False)):
        graph.append(f"[{audio_label}]loudnorm=I=-16:TP=-1.5:LRA=11[anormalized]")
        audio_label = "anormalized"
    fade = _clamp(p.get("fade"), 0, 3, 0)
    if fade:
        graph.append(f"[{video_label}]fade=t=in:d={min(fade, duration / 2):.3f},fade=t=out:st={max(0, duration-fade):.3f}:d={min(fade, duration/2):.3f}[vout]")
        graph.append(f"[{audio_label}]afade=t=in:d={min(fade, duration / 2):.3f},afade=t=out:st={max(0, duration-fade):.3f}:d={min(fade, duration/2):.3f}[aout]")
        video_label, audio_label = "vout", "aout"
    else:
        pass
    overlay = str(p.get("overlay") or "").strip()[:180]
    raw_words = p.get("caption_words") if isinstance(p.get("caption_words"), list) else []
    word_events = []
    for word in raw_words[:30000]:
        if not isinstance(word, dict) or not str(word.get("word") or "").strip():
            continue
        start = _clamp(word.get("start"), 0, duration, -1)
        end = _clamp(word.get("end"), 0, duration, -1)
        if start >= 0 and end > start:
            word_events.append({"start": start, "end": end, "word": str(word["word"])[:100]})
    if overlay or word_events:
        folder = WORK / job.id
        folder.mkdir(parents=True, exist_ok=True)
        ass = folder / "captions.ass"
        caption_style = str(p.get("caption_style") or "creator")
        caption_position = p.get("caption_position") if p.get("caption_position") in ("top", "middle", "bottom") else "bottom"
        caption_margin = (0.20 if height > width else 0.10) if caption_position == "bottom" else 0.12 if caption_position == "top" else 0.0
        overlay_position = p.get("overlay_position") if p.get("overlay_position") in ("top", "middle", "bottom") else "top"
        ass.write_text(captions.word_captions(
            word_events, width, height, style_name=caption_style,
            position=caption_position, margin_v=caption_margin, title_position=overlay_position,
            per_line=int(_clamp(p.get("caption_words_per_line"), 1, 5, 3)),
            highlight=bool(p.get("caption_highlight", True)),
            font_size=_clamp(p.get("caption_size"), .035, .085, .055),
            text_color=p.get("caption_text_color"), highlight_color=p.get("caption_highlight_color"),
            animation=p.get("caption_animation") if p.get("caption_animation") in ("none", "pop", "bounce", "fade") else "none",
            title=overlay, title_end=duration, duration=duration,
        ), encoding="utf-8")
        graph.append(f"[{video_label}]subtitles='{captions.ffmpeg_path(ass)}'[vcaption]")
        video_label = "vcaption"

    dst = jobs.next_output_path(OUT_VIDEO_EDIT, "timeline", "mp4")
    job.message = "Сборка таймлайна и цветокоррекция…"
    job.progress = 0.15
    args += ["-filter_complex", ";".join(graph), "-map", f"[{video_label}]", "-map", f"[{audio_label}]",
             "-t", f"{duration:.4f}", "-r", f"{fps:.3f}", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(dst)]
    t0 = time.time()
    media.run(args, timeout=7200)
    job.message = "Проверяю разрешение, звук, тёмные кадры и целостность экспорта…"
    job.progress = 0.88
    review_dir = OUT_VIDEO_EDIT / ".quality" / dst.stem
    quality = await asyncio.to_thread(_render_quality_check, dst, duration, width, height,
                                      transition_report, review_dir, audio_expectation)
    reframe_summary = {key: value for key, value in reframe_report.items() if key != "source_tracks"}
    quality["reframe"] = reframe_summary
    if not quality["decode_ok"]:
        raise RuntimeError("Экспорт создан, но проверка обнаружила повреждённые или нечитаемые кадры. Исходники сохранены.")
    qa_frames = [{"path": str(ROOT / item["path"]), "time": item["time"]}
                 for item in quality.get("review_frames", []) if isinstance(item, dict)]
    review_language = str(p.get("caption_language") or "ru")[:12]
    if qa_frames:
        job.message, job.progress = "Локально просматриваю выборочные кадры экспорта…", 0.92
        try:
            await llm.ensure(lambda message: setattr(job, "message", message))
            try:
                quality["visual_review"] = await llm.review_export_frames(qa_frames, review_language, width, height)
            finally:
                await resources.after_job("llm")
        except Exception as exc:
            log.warning("Local visual export review unavailable for %s: %s", dst, exc)
            quality["visual_review"] = {"available": False, "language": review_language,
                                         "sampled_frames": len(qa_frames), "notes": []}
    rel = jobs.rel(dst)
    job.gen_id = db.add_generation(type="video", engine="ffmpeg", model="Timeline Editor",
                                   prompt=str(p.get("assistant_note") or "Timeline edit")[:300],
                                   params={"segments": len(normalized), "duration": duration, "aspect": aspect,
                                           "grade": grade, "overlay": overlay, "fps": fps,
                                           "normalize_audio": bool(p.get("normalize_audio", False)),
                                           "subject_follow": reframe_summary},
                                   inputs={"sources": [jobs.rel(s) for s in sources]}, outputs=[rel],
                                   status="done", seconds=round(time.time() - t0, 1))
    job.progress = 0.95
    visual_review = quality.get("visual_review", {})
    job.message = ("Экспорт готов; проверьте отмеченные AI-визуальные замечания." if visual_review.get("notes") else
                   "Экспорт готов; AI не отметил явных проблем на просмотренных кадрах." if visual_review.get("available") else
                   "Экспорт готов; просмотрите контрольные кадры вручную.")
    job.result = {"outputs": [rel], "duration": round(duration, 2), "width": width, "height": height,
                  "fps": fps, "segments": len(normalized), "quality": quality, "reframe": reframe_report}
    if 'folder' in locals():
        shutil.rmtree(folder, ignore_errors=True)
    if track_work:
        shutil.rmtree(track_work, ignore_errors=True)


EXTRA_RUNNERS = {"video_timeline_render": run_timeline_render, "video_transcribe": run_timeline_transcription,
                 "audio_sfx": run_sfx_generation, "video_face_blur": run_video_face_blur,
                 "video_mask_track": run_video_mask_track,
                 "model_install": run_model_install}
