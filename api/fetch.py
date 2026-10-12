"""Download a video from a link (YouTube and ~1000 other sites) with yt-dlp, into temp/uploads.

Only http(s) links, single videos (no playlists), up to 1080p H.264/AAC MP4 by default.
Only download videos you have the right to use.
"""
import asyncio
import re
import time
import uuid
from pathlib import Path

import db
from jobs import rel
from settings import APPS, UPLOADS

YTDLP = APPS / "yt-dlp" / "yt-dlp.exe"
DENO = APPS / "deno" / "deno.exe"
FFMPEG_DIR = APPS / "ffmpeg" / "bin"
CREATE_NO_WINDOW = 0x08000000


def _ts(v) -> str:
    return v if isinstance(v, str) and ":" in v else time.strftime("%H:%M:%S", time.gmtime(float(v)))


async def run_fetch_url(job):
    p = dict(job.params)
    url = (p.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("Please paste a full http(s) link.")
    height = int(p.get("max_height", 1080))
    stem = f"url_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    out_tmpl = str(UPLOADS / f"{stem}.%(ext)s")
    args = [str(YTDLP), "--no-playlist", "--newline", "--no-part", "--restrict-filenames",
            "--js-runtimes", f"deno:{DENO}", "--ffmpeg-location", str(FFMPEG_DIR),
            "-f", f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={height}]+ba/b[height<={height}]/b",
            "--merge-output-format", "mp4", "--remux-video", "mp4", "--max-filesize", "6G",
            "--print", "after_move:filepath", "--print", "before_dl:title:%(title)s", "-o", out_tmpl]
    if p.get("start") or p.get("end"):
        args += ["--download-sections", f"*{_ts(p.get('start') or 0)}-{_ts(p.get('end') or '99:59:59')}",
                 "--force-keyframes-at-cuts"]
    args.append(url)
    job.message, job.progress = "Contacting site…", 0.02
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                creationflags=CREATE_NO_WINDOW)
    path, title, tail = None, None, []
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        s = line.decode("utf-8", "replace").strip()
        tail = (tail + [s])[-8:]
        if job.cancel_requested:
            proc.kill()
            raise RuntimeError("Download cancelled.")
        m = re.search(r"\[download\]\s+([\d.]+)%.*?(?:at\s+(\S+))?\s*(?:ETA\s+(\S+))?", s)
        if m:
            job.progress = min(0.97, float(m[1]) / 100)
            job.message = f"Downloading {m[1]}%" + (f" · {m[2]}" if m[2] else "") + (f" · ETA {m[3]}" if m[3] else "")
        elif s.startswith("title:"):
            title = s[6:]
        elif s.endswith(".mp4") and Path(s).exists():
            path = Path(s)
        elif "[Merger]" in s or "[VideoRemuxer]" in s:
            job.message = "Merging video and audio…"
    await proc.wait()
    if proc.returncode != 0 or not path:
        err = next((x for x in reversed(tail) if "ERROR" in x), tail[-1] if tail else "unknown error")
        raise RuntimeError("Could not download this link: " + err.replace("ERROR:", "").strip()[:300])
    r = rel(path)
    job.gen_id = db.add_generation(type="video", engine="yt-dlp", model="Downloaded video", prompt=title or url,
                                   params={"url": url, "start": p.get("start"), "end": p.get("end")},
                                   inputs={"url": url}, outputs=[r], status="done", seconds=round(time.time() - job.started, 1))
    job.result = {"outputs": [r], "path": r, "title": title, "seconds": round(time.time() - job.started, 1)}
