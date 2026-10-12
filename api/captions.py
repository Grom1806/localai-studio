"""ASS caption generation (rendered by ffmpeg/libass).

Styles: minimal, bold, creator, podcast. Word-level highlighting uses per-word events so the current word
changes colour exactly on its timestamp. Also used for dubbing subtitles (original / translated / both).
"""
from pathlib import Path

STYLES = {
    #            font,            size, primary,    highlight,  outline, shadow, box,   bold, upper
    "minimal": {"font": "Segoe UI", "size": 0.050, "color": "FFFFFF", "hl": "FFFFFF", "outline": 2, "shadow": 0,
                "box": False, "bold": False, "upper": False},
    "bold":    {"font": "Arial Black", "size": 0.062, "color": "FFFFFF", "hl": "00E5FF", "outline": 5, "shadow": 0,
                "box": False, "bold": True, "upper": True},
    "creator": {"font": "Segoe UI Black", "size": 0.066, "color": "FFFFFF", "hl": "34D399", "outline": 4,
                "shadow": 3, "box": False, "bold": True, "upper": False},
    "movie":   {"font": "Georgia", "size": 0.050, "color": "FFF3C4", "hl": "FFF3C4", "outline": 2, "shadow": 2,
                "box": False, "bold": False, "upper": False},
    "subtitle": {"font": "Segoe UI", "size": 0.046, "color": "FFFFFF", "hl": "FFFFFF", "outline": 0, "shadow": 0,
                 "box": True, "bold": False, "upper": False},
    "opus":    {"font": "Arial Black", "size": 0.058, "color": "FFFFFF", "hl": "FFD400", "outline": 6, "shadow": 0,
                "box": False, "bold": True, "upper": True, "hl2": "3CE66E"},
    "podcast": {"font": "Segoe UI Semibold", "size": 0.048, "color": "FFFFFF", "hl": "FBBF24", "outline": 0,
                "shadow": 0, "box": True, "bold": True, "upper": False},
    "clean":  {"font": "Segoe UI Semibold", "size": 0.052, "color": "FFFFFF", "hl": "FFFFFF", "outline": 2,
                "shadow": 1, "box": False, "bold": True, "upper": False},
    "reels":  {"font": "Arial Black", "size": 0.064, "color": "FFFFFF", "hl": "FFD84D", "outline": 5,
                "shadow": 1, "box": False, "bold": True, "upper": False},
    "neon":   {"font": "Arial Black", "size": 0.060, "color": "FFFFFF", "hl": "BFA7FF", "outline": 4,
                "shadow": 3, "box": False, "bold": True, "upper": False},
    "cinematic": {"font": "Georgia", "size": 0.048, "color": "FFF4DB", "hl": "FFF4DB", "outline": 2,
                  "shadow": 2, "box": False, "bold": False, "upper": False},
    "boxed":  {"font": "Segoe UI Semibold", "size": 0.050, "color": "FFFFFF", "hl": "8FE6D1", "outline": 1,
                "shadow": 0, "box": True, "bold": True, "upper": False},
    "outline": {"font": "Arial", "size": 0.056, "color": "FFFFFF", "hl": "75D8FF", "outline": 5,
                "shadow": 2, "box": False, "bold": True, "upper": False},
}


def _bgr(hex_rgb: str, alpha: int = 0) -> str:
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _t(sec: float) -> str:
    sec = max(0.0, sec)
    h, m = int(sec // 3600), int(sec % 3600 // 60)
    s = sec - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def header(w: int, h: int, style: dict, position: str = "bottom", margin_v: float = 0.14, title_style=True,
           title_margin: float | None = None, title_position: str = "top") -> str:
    size = int(h * style["size"]) if style["size"] < 1 else int(style["size"])
    align = {"bottom": 2, "middle": 5, "top": 8}[position]
    mv = int(h * margin_v) if position != "middle" else 0
    border = 3 if style["box"] else 1
    back = _bgr("000000", 0x60) if style["box"] else _bgr("000000", 0x80)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Cap,{style['font']},{size},{_bgr(style['color'])},{_bgr(style['hl'])},{_bgr('000000')},{back},"
        f"{-1 if style['bold'] else 0},0,0,0,100,100,0,0,{border},{style['outline'] if not style['box'] else 12},"
        f"{style['shadow']},{align},{int(w * 0.07)},{int(w * 0.07)},{mv},1",
    ]
    if title_style:
        ts = int(h * 0.032)
        title_align = {"top": 8, "middle": 5, "bottom": 2}.get(title_position, 8)
        safe_title_margin = title_margin if title_margin is not None else {"top": 0.12, "middle": 0.0, "bottom": 0.20}.get(title_position, 0.12)
        # Headline: black bold text on a white box, positioned inside the chosen safe margin.
        lines.append(f"Style: Title,Segoe UI Semibold,{ts},{_bgr('111111')},{_bgr('111111')},{_bgr('FFFFFF')},"
                     f"{_bgr('FFFFFF')},-1,0,0,0,100,100,0,0,3,{max(8, int(ts * 0.55))},0,{title_align},{int(w * 0.16)},"
                     f"{int(w * 0.16)},{int(h * safe_title_margin)},1")
        lines.append(f"Style: Sub2,{style['font']},{int(size * 0.8)},{_bgr('D8D8D8')},{_bgr('FFFFFF')},"
                     f"{_bgr('000000')},{back},0,0,0,0,100,100,0,0,{border},{max(1, style['outline'] - 1)},0,"
                     f"{align},{int(w * 0.07)},{int(w * 0.07)},{mv + int(size * 1.35)},1")
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    return "\n".join(lines) + "\n"


def group_words(words: list[dict], per_line: int = 3, max_gap: float = 0.8) -> list[list[dict]]:
    """Group words into short caption chunks, breaking on punctuation and pauses."""
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= per_line or w["start"] - cur[-1]["end"] > max_gap):
            groups.append(cur)
            cur = []
        cur.append(w)
        if w["word"].strip()[-1:] in ".!?…," and len(cur) >= max(1, per_line // 2):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def word_captions(words: list[dict], w: int, h: int, style_name="creator", position="bottom", per_line=3,
                  highlight=True, box=None, outline=None, font_size=None, title=None, title_end=None,
                  duration=None, margin_v=0.14, title_margin: float | None = None, text_color=None,
                  highlight_color=None, animation="none", title_position="top") -> str:
    """words: [{start, end, word}] relative to the clip. Returns full ASS text."""
    st = dict(STYLES.get(style_name, STYLES["creator"]))
    if text_color and len(str(text_color)) == 6 and all(c in "0123456789abcdefABCDEF" for c in str(text_color)):
        st["color"] = str(text_color).upper()
    if highlight_color and len(str(highlight_color)) == 6 and all(c in "0123456789abcdefABCDEF" for c in str(highlight_color)):
        st["hl"] = str(highlight_color).upper()
    animation_tag = {
        "pop": r"{\fscx122\fscy122\t(0,145,\fscx100\fscy100)}",
        "bounce": r"{\fscx88\fscy118\t(0,100,\fscx108\fscy94)\t(100,230,\fscx100\fscy100)}",
        "fade": r"{\fad(110,70)}",
    }.get(animation, "")
    if box is not None:
        st["box"] = bool(box)
    if outline is not None:
        st["outline"] = int(outline)
    if font_size:
        st["size"] = float(font_size)
    out = [header(w, h, st, position, margin_v=margin_v, title_margin=title_margin,
                  title_position=title_position)]
    for grp in group_words(words, per_line):
        texts = [(_esc(x["word"].strip().upper() if st["upper"] else x["word"].strip())) for x in grp]
        g_start, g_end = grp[0]["start"], max(grp[-1]["end"], grp[0]["start"] + 0.3)
        if not highlight:
            out.append(f"Dialogue: 0,{_t(g_start)},{_t(g_end)},Cap,,0,0,0,,{animation_tag}{' '.join(texts)}")
            continue
        hl = _bgr(st["hl"])
        for k, wd in enumerate(grp):
            a = wd["start"] if k else g_start
            b = grp[k + 1]["start"] if k + 1 < len(grp) else g_end
            if b <= a:
                continue
            parts = [(f"{{\\c{hl}}}{t}{{\\r}}" if j == k else t) for j, t in enumerate(texts)]
            out.append(f"Dialogue: 0,{_t(a)},{_t(b)},Cap,,0,0,0,,{animation_tag}{' '.join(parts)}")
    if title:
        out.append(f"Dialogue: 1,{_t(0)},{_t(title_end or duration or 9999)},Title,,0,0,0,,{_esc(title)}")
    return "".join(x if x.endswith("\n") else x + "\n" for x in out)


def subtitle_track(segments: list[dict], w: int, h: int, mode: str = "translated", style: str = "minimal") -> str:
    """Sentence subtitles for dubbing. segments: [{start, end, text, translation}]. mode: original|translated|both"""
    st = dict(STYLES.get(style, STYLES["minimal"]))
    st["size"] = min(st["size"], 0.05)
    out = [header(w, h, st, "bottom", margin_v=0.06)]
    for s in segments:
        a, b = _t(s["start"]), _t(s["end"])
        if mode in ("translated", "both") and s.get("translation"):
            out.append(f"Dialogue: 0,{a},{b},Cap,,0,0,0,,{_esc(s['translation'])}\n")
        if mode == "original" or (mode == "both"):
            style = "Sub2" if mode == "both" else "Cap"
            out.append(f"Dialogue: 0,{a},{b},{style},,0,0,0,,{_esc(s['text'])}\n")
    return "".join(out)


def srt(segments: list[dict], key="translation") -> str:
    def t(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{i + 1}\n{t(s['start'])} --> {t(s['end'])}\n{s.get(key) or s.get('text', '')}\n"
                     for i, s in enumerate(segments))


def ffmpeg_path(p: Path) -> str:
    """Escape a Windows path for the ffmpeg subtitles/ass filter."""
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
