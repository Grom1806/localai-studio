"""Verify a dub: speech language/text (Whisper), background preserved (Demucs stems correlation), frames."""
import json
import subprocess
import sys
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

FF = r"C:\LocalAI\apps\ffmpeg\bin\ffmpeg.exe"
orig, dub = sys.argv[1], sys.argv[2]
W = Path(r"C:\LocalAI\temp\verify")
W.mkdir(exist_ok=True)
M = "http://127.0.0.1:8104"
for name, src in (("orig", orig), ("dub", dub)):
    subprocess.run([FF, "-y", "-v", "error", "-i", src, "-vn", "-ac", "2", "-ar", "44100", str(W / f"{name}.wav")], check=True)
    httpx.post(M + "/separate", json={"path": str(W / f"{name}.wav"), "out_dir": str(W / name)}, timeout=900)
tr = httpx.post(M + "/transcribe", json={"path": str(W / "dub" / "vocals.wav")}, timeout=900).json()


def env(p):
    x, sr = sf.read(p, dtype="float32")
    x = x.mean(1) if x.ndim > 1 else x
    h = sr // 10
    return np.array([np.sqrt(np.mean(x[i:i + h] ** 2)) for i in range(0, len(x) - h, h)])


a, b = env(W / "orig" / "background.wav"), env(W / "dub" / "background.wav")
n = min(len(a), len(b))
print(json.dumps({
    "dub_language": tr["language"], "lang_prob": tr["language_probability"],
    "dub_text_start": " ".join(s["text"] for s in tr["segments"])[:260],
    "background_envelope_corr": round(float(np.corrcoef(a[:n], b[:n])[0, 1]), 3),
    "background_level_ratio": round(float(b[:n].mean() / max(1e-9, a[:n].mean())), 2),
}, ensure_ascii=False, indent=1))
