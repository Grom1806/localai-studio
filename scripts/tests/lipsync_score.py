"""Objective lip-sync check: correlation between mouth opening (MediaPipe) and speech loudness over time.
Also tests audio/video offsets (-0.2..+0.2 s) and reports the best lag. usage: lipsync_score.py video.mp4"""
import json
import subprocess
import sys

import httpx
import numpy as np

FF = r"C:\LocalAI\apps\ffmpeg\bin\ffmpeg.exe"
video = sys.argv[1]
tr = httpx.post("http://127.0.0.1:8104/track", json={"path": video, "sample_fps": 25}, timeout=600).json()
ts = np.array([s["t"] for s in tr["samples"]])
mouth = np.array([max((f["mouth"] for f in s["faces"]), default=np.nan) for s in tr["samples"]])
raw = subprocess.run([FF, "-v", "error", "-i", video, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                     capture_output=True).stdout
a = np.frombuffer(raw, dtype=np.float32)
win = 640  # 40 ms
env = np.array([np.sqrt(np.mean(a[i:i + win] ** 2)) for i in range(0, len(a) - win, win)])
et = np.arange(len(env)) * win / 16000 + 0.02
ok = ~np.isnan(mouth)
res = {}
for lag in np.arange(-0.2, 0.21, 0.04):
    e = np.interp(ts[ok] + lag, et, env)
    res[round(float(lag), 2)] = round(float(np.corrcoef(mouth[ok], e)[0, 1]), 3)
best = max(res, key=res.get)
print(json.dumps({"face_found_ratio": round(float(ok.mean()), 2), "corr_at_0": res.get(0.0),
                  "best_lag_s": best, "best_corr": res[best], "mouth_range": [round(float(np.nanmin(mouth)), 4),
                  round(float(np.nanmax(mouth)), 4)]}))
