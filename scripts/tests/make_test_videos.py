"""Build realistic test videos from local assets: TTS speech + music bed + portrait visuals (ffmpeg)."""
import json
import subprocess
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

FF = r"C:\LocalAI\apps\ffmpeg\bin\ffmpeg.exe"
T = Path(r"C:\LocalAI\temp\testvid")
T.mkdir(parents=True, exist_ok=True)
SR = 24000


def tts(engine, port, text, out, **kw):
    body = {"text": text, "out_path": str(out), "language": "en", "params": {}, **kw}
    r = httpx.post(f"http://127.0.0.1:{port}/generate", json=body, timeout=900)
    r.raise_for_status()
    x, sr = sf.read(out, dtype="float32")
    if sr != SR:
        subprocess.run([FF, "-y", "-v", "error", "-i", out, "-ar", str(SR), "-ac", "1", str(out) + ".r.wav"], check=True)
        x, _ = sf.read(str(out) + ".r.wav", dtype="float32")
    return x


def music_bed(seconds, path):
    """Soft synthetic pad + light noise ambience, clearly audible under the voice (for background tests)."""
    t = np.arange(int(seconds * 48000)) / 48000
    chords = [(220, 277.2, 329.6), (196, 246.9, 293.7), (174.6, 220, 261.6), (196, 246.9, 311.1)]
    y = np.zeros_like(t)
    for k, ch in enumerate(chords * int(seconds / 16 + 1)):
        a, b = k * 4.0, (k + 1) * 4.0
        m = (t >= a) & (t < b)
        env = np.clip(np.minimum(t[m] - a, b - t[m]) / 0.8, 0, 1)
        for f in ch:
            y[m] += 0.05 * env * np.sin(2 * np.pi * f * t[m])
    y += 0.01 * np.random.default_rng(1).standard_normal(len(t))
    sf.write(path, np.stack([y, y], 1).astype(np.float32), 48000)


def mix(voice_wav, seconds, out):
    bed = T / "bed.wav"
    music_bed(seconds + 1, bed)
    subprocess.run([FF, "-y", "-v", "error", "-i", voice_wav, "-i", bed, "-filter_complex",
                    "[0:a]aresample=48000,volume=1.0[v];[1:a]volume=0.55[m];[v][m]amix=inputs=2:duration=first:normalize=0",
                    "-ar", "48000", "-ac", "2", out], check=True)


# 1) talking-head monologue (Chatterbox default voice)
text = open(r"C:\LocalAI\temp\test_monologue.txt", encoding="utf-8").read()
parts = [p for p in text.replace("\n", " ").split(". ") if p.strip()]
chunks, cur = [], ""
for p in parts:
    cur = (cur + ". " + p).strip(". ") if cur else p
    if len(cur) > 180:
        chunks.append(cur + ".")
        cur = ""
if cur:
    chunks.append(cur if cur.endswith((".", "?", "!")) else cur + ".")
audio = []
for k, c in enumerate(chunks):
    audio.append(tts("chatterbox", 8101, c, T / f"mono_{k}.wav"))
    audio.append(np.zeros(int(0.45 * SR), np.float32))
voice = np.concatenate(audio)
sf.write(T / "mono_voice.wav", voice, SR)
dur = len(voice) / SR
mix(T / "mono_voice.wav", dur, T / "mono_mix.wav")
subprocess.run([FF, "-y", "-v", "error", "-loop", "1", "-i", r"C:\LocalAI\outputs\images\2026-09-25_zimage_0001.png",
                "-i", T / "mono_mix.wav", "-filter_complex",
                "[0:v]split[a][b];[a]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,boxblur=20[bg];"
                "[b]scale=-2:720,zoompan=z='min(1+0.0004*on,1.08)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x720:fps=25[fg];"
                "[bg][fg]overlay=(W-w)/2:0,format=yuv420p[v]",
                "-map", "[v]", "-map", "1:a", "-t", f"{dur:.2f}", "-r", "25", "-c:v", "libx264", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k", r"C:\LocalAI\outputs\videos\test_talkinghead_en.mp4"], check=True)
print("talking head:", round(dur, 1), "s")

# 2) two-person podcast (A: Chatterbox female default, B: Qwen3-TTS Ryan)
pod = json.load(open(r"C:\LocalAI\temp\test_podcast.json", encoding="utf-8"))["lines"]
audio = []
for k, line in enumerate(pod):
    if line["speaker"] == "A":
        x = tts("chatterbox", 8101, line["text"], T / f"pod_{k}.wav")
    else:
        x = tts("qwen", 8102, line["text"], T / f"pod_{k}.wav", speaker="Ryan")
    audio.append(x)
    audio.append(np.zeros(int(0.35 * SR), np.float32))
voice = np.concatenate(audio)
sf.write(T / "pod_voice.wav", voice, SR)
dur = len(voice) / SR
mix(T / "pod_voice.wav", dur, T / "pod_mix.wav")
subprocess.run([FF, "-y", "-v", "error", "-loop", "1", "-i", r"C:\LocalAI\outputs\images\2026-09-25_zimage_0001.png",
                "-loop", "1", "-i", r"C:\LocalAI\outputs\images\2026-09-25_zimage_0007.png", "-i", T / "pod_mix.wav",
                "-filter_complex", "[0:v]scale=960:1080:force_original_aspect_ratio=increase,crop=960:1080[l];"
                "[1:v]scale=960:1080:force_original_aspect_ratio=increase,crop=960:1080[r];[l][r]hstack,format=yuv420p[v]",
                "-map", "[v]", "-map", "2:a", "-t", f"{dur:.2f}", "-r", "25", "-c:v", "libx264", "-crf", "22",
                "-c:a", "aac", "-b:a", "192k", r"C:\LocalAI\outputs\videos\test_podcast_en.mp4"], check=True)
print("podcast:", round(dur, 1), "s")
