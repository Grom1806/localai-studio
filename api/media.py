"""FFmpeg helpers shared by the video tools (probe, segmenting, concat, audio mux, captions)."""
import json
import math
import subprocess
from pathlib import Path

from settings import FFMPEG, FFPROBE

CREATE_NO_WINDOW = 0x08000000


def run(args, timeout=None):
    r = subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", *map(str, args)],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-800:]}")
    return r


def probe(path) -> dict:
    r = subprocess.run([str(FFPROBE), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"Could not read the video: {r.stderr.strip()[-300:]}")
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    dur = float(d["format"].get("duration") or 0)
    info = {"duration": dur, "has_audio": a is not None, "has_video": v is not None}
    if v:
        num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "25/1").split("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 25.0
        frames = int(v.get("nb_frames") or 0) or int(round(dur * fps))
        info.update(width=int(v["width"]), height=int(v["height"]), fps=round(fps, 3), frames=frames)
    return info


def analyze_frame_levels(frame_paths: list) -> dict:
    """Measure the editor's already-sampled frames and suggest only restrained exposure/contrast fixes."""
    import re
    import statistics

    values = {key: [] for key in ("YLOW", "YAVG", "YHIGH")}
    for path in frame_paths[:12]:
        try:
            result = subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "info", "-i", str(path),
                                     "-frames:v", "1", "-vf",
                                     "scale=64:64:force_original_aspect_ratio=decrease,signalstats,metadata=print",
                                     "-an", "-f", "null", "-"], capture_output=True, text=True,
                                    errors="replace", creationflags=CREATE_NO_WINDOW, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        for key in values:
            match = re.search(rf"lavfi\.signalstats\.{key}=([\d.]+)", result.stderr)
            if match:
                values[key].append(float(match.group(1)))

    sample_count = min((len(items) for items in values.values()), default=0)
    if sample_count < 3:
        return {"available": False, "reason": "Not enough decoded frames for reliable level analysis.",
                "sample_count": sample_count}

    low, avg, high = (statistics.median(values[key][:sample_count]) for key in ("YLOW", "YAVG", "YHIGH"))
    brightness, contrast = 0.0, 1.0
    if avg < 90 and high < 165 and low < 28:
        brightness = round(min(0.08, max(0.03, (105 - avg) / 500)), 3)
    elif avg > 210 and low > 125:
        brightness = -round(min(0.07, max(0.025, (avg - 195) / 600)), 3)
    spread = high - low
    if spread < 52 and 65 <= avg <= 195:
        contrast = 1.05
    elif spread > 195:
        contrast = 0.97
    return {"available": True, "sample_count": sample_count, "median_luma": round(avg, 1),
            "median_luma_low": round(low, 1), "median_luma_high": round(high, 1),
            "recommended_grade": {"brightness": brightness, "contrast": contrast},
            "adjustment_recommended": brightness != 0.0 or contrast != 1.0}


def detect_bars(path, start: float, dur: float, w: int, h: int):
    """Find burned-in black bars (letterbox/pillarbox). Returns (cw, ch, x, y) or None when there are none."""
    import re
    import statistics
    r = subprocess.run([str(FFMPEG), "-hide_banner", "-ss", f"{start:.2f}", "-t", f"{min(dur, 20):.2f}", "-i", str(path),
                        "-vf", "fps=2,cropdetect=limit=24:round=2:reset=1", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    found = [tuple(map(int, m)) for m in re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)]
    if len(found) < 3:
        return None
    # the widest detected area over the sample (dark scenes shrink single detections)
    cw, ch = max(f[0] for f in found), max(f[1] for f in found)
    x = int(statistics.median(f[2] for f in found if f[0] == cw)) // 2 * 2
    y = int(statistics.median(f[3] for f in found if f[1] == ch)) // 2 * 2
    if cw * ch > 0.92 * w * h or cw < w * 0.4 or ch < h * 0.4:
        return None                                        # no bars worth removing, or a bad detection
    return cw, ch, x, y


def plan_segments(frames: int, out_w: int, out_h: int, multiplier: int = 1, budget_gb: float = 3.0,
                  overlap: int = 0) -> list[tuple[int, int]]:
    """Frame ranges [a, b) sized so one segment's output frames fit in ~budget_gb of float32 RAM."""
    per_frame = out_w * out_h * 3 * 4 * multiplier
    seg = max(8, int(budget_gb * 1e9 / per_frame))
    seg = min(seg, frames)
    ranges, a = [], 0
    while a < frames:
        b = min(frames, a + seg)
        ranges.append((a, b))
        if b >= frames:
            break
        a = b - overlap
    return ranges


def cut_frames(src, a: int, b: int, dst):
    """Frame-accurate, video-only segment [a, b) as near-lossless H.264."""
    run(["-i", src, "-an", "-vf", f"trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS",
         "-c:v", "libx264", "-crf", "10", "-preset", "veryfast", "-pix_fmt", "yuv420p", dst])


def concat_with_audio(segments: list, dst, audio_src=None, drop_first_frame_after_first=False, fps=None,
                      crf=16, audio_offset=0.0, audio_duration=None):
    """Concatenate video segments (re-encode) and mux the original audio track if present."""
    args = []
    for s in segments:
        args += ["-i", s]
    n = len(segments)
    parts = []
    for i in range(n):
        f = f"[{i}:v]"
        if drop_first_frame_after_first and i > 0:
            parts.append(f"{f}trim=start_frame=1,setpts=PTS-STARTPTS[v{i}]")
        else:
            parts.append(f"{f}setpts=PTS-STARTPTS[v{i}]")
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vout]")
    fc = ";".join(parts)
    if audio_src:
        if audio_offset:
            args += ["-ss", f"{audio_offset:.3f}"]
        if audio_duration:
            args += ["-t", f"{audio_duration:.3f}"]
        args += ["-i", audio_src]
    args += ["-filter_complex", fc, "-map", "[vout]"]
    if audio_src:
        args += ["-map", f"{n}:a:0?", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    if fps:
        args += ["-r", str(fps)]
    args += ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", dst]
    run(args)


def fit_size(w: int, h: int, max_long: int, mult: int = 16) -> tuple[int, int]:
    scale = max_long / max(w, h)
    return (max(mult, int(round(w * scale / mult)) * mult), max(mult, int(round(h * scale / mult)) * mult))


def prepare_clip(src, dst, fps: float, width: int, height: int, max_frames: int, start: float = 0.0):
    """Resample to fps, crop/scale to width×height, keep at most max_frames (video only)."""
    vf = (f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
          f"crop={width}:{height},setsar=1")
    run(["-ss", f"{start:.3f}", "-i", src, "-an", "-vf", vf, "-frames:v", max_frames,
         "-c:v", "libx264", "-crf", "10", "-preset", "veryfast", "-pix_fmt", "yuv420p", dst])


def wan_frames(n: int, cap: int = 81) -> int:
    """Wan/Bernini needs 4k+1 frames."""
    n = min(n, cap)
    return max(5, ((n - 1) // 4) * 4 + 1)


def ceil_to(x, m):
    return int(math.ceil(x / m) * m)
