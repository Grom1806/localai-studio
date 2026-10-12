"""Compare the loudness envelopes of two audio sources (does the model keep the input speech?)."""
import json
import subprocess
import sys

import numpy as np

FF = r"C:\LocalAI\apps\ffmpeg\bin\ffmpeg.exe"


def env(path):
    raw = subprocess.run([FF, "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.float32)
    return np.array([np.sqrt(np.mean(a[i:i + 640] ** 2)) for i in range(0, len(a) - 640, 640)])


x, y = env(sys.argv[1]), env(sys.argv[2])
n = min(len(x), len(y))
print(json.dumps({"envelope_corr": round(float(np.corrcoef(x[:n], y[:n])[0, 1]), 3), "len_a": len(x), "len_b": len(y)}))
