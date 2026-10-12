"""LocalAI media worker (env-media, 127.0.0.1:8104).

/transcribe  faster-whisper large-v3-turbo (CTranslate2, converted locally from models/tts/whisper-large-v3-turbo)
             -> segments + word timestamps, automatic language detection
/separate    Demucs htdemucs -> vocals.wav + background.wav (music, effects, ambience)
/diarize     pyannote speaker-diarization-community-1 -> speaker turns (skipped by callers for 1 speaker)
/track       MediaPipe face detection + mouth activity -> smoothed crop path for auto-reframing
/unload      free every model (CUDA memory released)
"""
import gc
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
os.environ.setdefault("HF_HOME", str(ROOT / "cache" / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TORCH_HOME", str(MODELS / "tools" / "torch_hub"))

# CTranslate2's Windows wheels link cuBLAS 12 / cuDNN 9: expose the pip-installed NVIDIA DLL folders.
_site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
for sub in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
    d = _site / sub / "bin"
    if d.exists():
        os.add_dll_directory(str(d))
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")

import numpy as np  # noqa: E402
import torch  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

WHISPER_HF = MODELS / "tts" / "whisper-large-v3-turbo"
WHISPER_CT2 = MODELS / "tools" / "whisper-large-v3-turbo-ct2"
DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"

log = logging.getLogger("media")
app = FastAPI()
lock = threading.Lock()
M = {"whisper": None, "demucs": None, "diar": None}
state = {"status": "idle", "error": None}


def free():
    for k in M:
        M[k] = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


# ─────────────────────────── transcription ───────────────────────────
def ensure_ct2():
    """Convert the already-downloaded HF Whisper once (int8_float16 ≈ 0.8 GB) instead of downloading a copy."""
    if (WHISPER_CT2 / "model.bin").exists():
        return
    import ctranslate2.converters
    log.info("converting %s -> CTranslate2", WHISPER_HF)
    ctranslate2.converters.TransformersConverter(
        str(WHISPER_HF), copy_files=["tokenizer.json", "preprocessor_config.json"]).convert(
        str(WHISPER_CT2), quantization="int8_float16", force=True)


def whisper():
    if M["whisper"] is None:
        ensure_ct2()
        from faster_whisper import WhisperModel
        M["whisper"] = WhisperModel(str(WHISPER_CT2), device="cuda", compute_type="int8_float16")
    return M["whisper"]


class TranscribeReq(BaseModel):
    path: str
    language: str | None = None      # None = auto-detect
    vad: bool = True
    beam_size: int = 5


@app.post("/transcribe")
def transcribe(r: TranscribeReq):
    with lock:
        t0 = time.time()
        try:
            state["status"] = "busy"
            segs, info = whisper().transcribe(r.path, language=r.language or None, beam_size=r.beam_size,
                                              word_timestamps=True, vad_filter=r.vad,
                                              vad_parameters={"min_silence_duration_ms": 400},
                                              condition_on_previous_text=False)
            out = []
            for s in segs:
                out.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(),
                            "words": [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word,
                                       "prob": round(w.probability, 3)} for w in (s.words or [])]})
            state["status"] = "ready"
            return {"language": info.language, "language_probability": round(info.language_probability, 3),
                    "duration": info.duration, "segments": out, "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            return _fail("transcription", e)


# ─────────────────────────── vocal separation ───────────────────────────
class SeparateReq(BaseModel):
    path: str
    out_dir: str
    model: str = "htdemucs"


@app.post("/separate")
def separate(r: SeparateReq):
    with lock:
        t0 = time.time()
        try:
            state["status"] = "busy"
            from demucs.api import Separator, save_audio
            if M["demucs"] is None or M["demucs"][0] != r.model:
                M["demucs"] = (r.model, Separator(model=r.model, device="cuda", segment=7.8, overlap=0.25))
            sep = M["demucs"][1]
            _, stems = sep.separate_audio_file(r.path)
            out = Path(r.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            vocals = stems["vocals"]
            background = sum(v for k, v in stems.items() if k != "vocals")
            save_audio(vocals, str(out / "vocals.wav"), samplerate=sep.samplerate)
            save_audio(background, str(out / "background.wav"), samplerate=sep.samplerate)
            state["status"] = "ready"
            return {"vocals": str(out / "vocals.wav"), "background": str(out / "background.wav"),
                    "samplerate": sep.samplerate, "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            return _fail("vocal separation", e)


# ─────────────────────────── diarization ───────────────────────────
class DiarizeReq(BaseModel):
    path: str
    num_speakers: int | None = None
    max_speakers: int | None = 6


@app.post("/diarize")
def diarize(r: DiarizeReq):
    with lock:
        t0 = time.time()
        try:
            state["status"] = "busy"
            if M["diar"] is None:
                from pyannote.audio import Pipeline
                M["diar"] = Pipeline.from_pretrained(DIARIZATION_REPO, token=True).to(torch.device("cuda"))
            import soundfile as sf
            wav, sr = sf.read(r.path, dtype="float32", always_2d=True)
            waveform = torch.from_numpy(wav.mean(axis=1, keepdims=True).T)  # in-memory: avoids torchcodec decode
            kw = {"num_speakers": r.num_speakers} if r.num_speakers else {"max_speakers": r.max_speakers}
            res = M["diar"]({"waveform": waveform, "sample_rate": sr}, **kw)
            ann = getattr(res, "exclusive_speaker_diarization", None) or getattr(res, "speaker_diarization", res)
            turns = [{"start": round(t.start, 3), "end": round(t.end, 3), "speaker": spk}
                     for t, _, spk in ann.itertracks(yield_label=True)]
            state["status"] = "ready"
            return {"turns": turns, "speakers": sorted({t["speaker"] for t in turns}),
                    "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            return _fail("speaker detection", e)


# ─────────────────────────── face / speaker tracking ───────────────────────────
class TrackReq(BaseModel):
    path: str
    start: float = 0.0
    end: float | None = None
    sample_fps: float = 6.0


@app.post("/track")
def track(r: TrackReq):
    """Per-sample face boxes (normalized) with a mouth-activity score, for active-speaker reframing."""
    with lock:
        t0 = time.time()
        try:
            import cv2
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
            cap = cv2.VideoCapture(r.path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            end = r.end or (cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps)
            step = max(1, int(round(fps / r.sample_fps)))
            cap.set(cv2.CAP_PROP_POS_MSEC, r.start * 1000)
            opts = vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(MODELS / "tools" / "mediapipe" / "face_landmarker.task")),
                running_mode=vision.RunningMode.VIDEO, num_faces=4, min_face_detection_confidence=0.5,
                min_face_presence_confidence=0.5, min_tracking_confidence=0.5)
            mesh = vision.FaceLandmarker.create_from_options(opts)
            samples, idx = [], 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                t = r.start + idx / fps
                if t > end:
                    break
                if idx % step == 0:
                    small = cv2.resize(frame, (640, int(640 * H / W)))
                    img = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
                    res = mesh.detect_for_video(img, int(t * 1000))
                    faces = []
                    for lm in (res.face_landmarks or []):
                        xs = [p.x for p in lm]
                        ys = [p.y for p in lm]
                        # mouth opening (inner lips 13/14) relative to face height
                        mouth = abs(lm[13].y - lm[14].y) / max(1e-3, max(ys) - min(ys))
                        faces.append({"x": (min(xs) + max(xs)) / 2, "y": (min(ys) + max(ys)) / 2,
                                      "w": max(xs) - min(xs), "h": max(ys) - min(ys), "mouth": round(mouth, 4)})
                    samples.append({"t": round(t, 3), "faces": faces})
                idx += 1
            cap.release()
            mesh.close()
            return {"width": W, "height": H, "fps": fps, "samples": samples, "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            return _fail("face tracking", e)


# ─────────────────────────── mouth paste-back (face-preserving lip sync) ───────────────────────────
class BlendReq(BaseModel):
    original: str          # original video (any fps / resolution)
    generated: str         # lip-synced generation, already resampled to the original fps and duration
    out: str               # output video (no audio)
    feather: float = 0.06  # mask feather relative to face height


# MediaPipe face-mesh indices: stable points for alignment (eye corners, nose bridge, cheeks) and the lower-face region
ALIGN = [33, 133, 362, 263, 168, 6, 197, 234, 454]
LOWER = [132, 58, 172, 136, 150, 149, 176, 148, 152, 377, 400, 378, 379, 365, 397, 288, 361, 323, 454, 356,
         0, 164, 2, 97, 326, 205, 425, 50, 280]


@app.post("/mouth_blend")
def mouth_blend(r: BlendReq):
    """Warp only the mouth/jaw region of the generated face onto the original frames (identity, eyes, hair,
    background, resolution and head motion all stay from the original)."""
    with lock:
        t0 = time.time()
        try:
            import cv2
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision

            def landmarker():
                o = vision.FaceLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=str(MODELS / "tools" / "mediapipe" / "face_landmarker.task")),
                    running_mode=vision.RunningMode.VIDEO, num_faces=1)
                return vision.FaceLandmarker.create_from_options(o)

            A, B = cv2.VideoCapture(r.original), cv2.VideoCapture(r.generated)
            fps = A.get(cv2.CAP_PROP_FPS) or 25
            W, H = int(A.get(cv2.CAP_PROP_FRAME_WIDTH)), int(A.get(cv2.CAP_PROP_FRAME_HEIGHT))
            la, lb = landmarker(), landmarker()
            import subprocess
            ff = str(ROOT / "apps" / "ffmpeg" / "bin" / "ffmpeg.exe")
            enc = subprocess.Popen([ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                                    "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                                    "-pix_fmt", "yuv420p", r.out], stdin=subprocess.PIPE)
            n = blended = 0
            prev_M = None
            while True:
                ok1, fa = A.read()
                ok2, fb = B.read()
                if not ok1:
                    break
                if ok2:
                    fb = cv2.resize(fb, (W, H)) if fb.shape[:2] != (H, W) else fb
                    ts = int(n * 1000 / fps)
                    ra = la.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                      data=cv2.cvtColor(fa, cv2.COLOR_BGR2RGB)), ts)
                    rb = lb.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                      data=cv2.cvtColor(fb, cv2.COLOR_BGR2RGB)), ts)
                    if ra.face_landmarks and rb.face_landmarks:
                        pa = np.array([[p.x * W, p.y * H] for p in ra.face_landmarks[0]], np.float32)
                        pb = np.array([[p.x * W, p.y * H] for p in rb.face_landmarks[0]], np.float32)
                        M, _ = cv2.estimateAffinePartial2D(pb[ALIGN], pa[ALIGN], method=cv2.LMEDS)
                        if M is not None:
                            prev_M = M if prev_M is None else 0.6 * M + 0.4 * prev_M   # temporal smoothing
                            warped = cv2.warpAffine(fb, prev_M, (W, H), flags=cv2.INTER_LINEAR,
                                                    borderMode=cv2.BORDER_REFLECT)
                            mask = np.zeros((H, W), np.float32)
                            hull = cv2.convexHull(pa[LOWER].astype(np.int32))
                            cv2.fillConvexPoly(mask, hull, 1.0)
                            face_h = float(pa[:, 1].max() - pa[:, 1].min())
                            k = max(3, int(face_h * r.feather) | 1)
                            mask = cv2.GaussianBlur(mask, (k * 2 + 1, k * 2 + 1), 0)[..., None]
                            # match colour of the pasted region to the original skin tone
                            sel = mask[..., 0] > 0.5
                            if sel.any():
                                diff = fa[sel].mean(0) - warped[sel].mean(0)
                                warped = np.clip(warped.astype(np.float32) + diff, 0, 255)
                            fa = (fa.astype(np.float32) * (1 - mask) + warped * mask).astype(np.uint8)
                            blended += 1
                enc.stdin.write(fa.tobytes())
                n += 1
            A.release()
            B.release()
            la.close()
            lb.close()
            enc.stdin.close()
            enc.wait()
            return {"out": r.out, "frames": n, "blended": blended, "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            return _fail("lip-sync blending", e)


# ─────────────────────────── service ───────────────────────────
def _fail(what, e):
    log.error("%s failed\n%s", what, traceback.format_exc())
    state["status"], state["error"] = "ready", str(e)
    msg = str(e)
    if isinstance(e, torch.cuda.OutOfMemoryError):
        free()
        return JSONResponse({"error": f"Not enough VRAM for {what}.", "code": "oom"}, status_code=507)
    if "gated" in msg.lower() or "401" in msg or "403" in msg or "accept" in msg.lower():
        return JSONResponse({"error": f"{what}: the model requires accepting its license on Hugging Face.",
                             "code": "gated", "detail": msg}, status_code=403)
    return JSONResponse({"error": f"{what} failed: {msg[:300]}", "code": "failed"}, status_code=500)


@app.get("/health")
def health():
    loaded = [k for k, v in M.items() if v is not None]
    return {"engine": "media", "status": state["status"], "loaded": bool(loaded), "models": loaded,
            "vram_mb": round(torch.cuda.memory_reserved() / 2**20) if torch.cuda.is_available() else 0,
            "error": state["error"], "pid": os.getpid()}


@app.post("/unload")
def unload():
    with lock:
        free()
        state["status"] = "idle"
        return health()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8104)
    a = ap.parse_args()
    (ROOT / "logs").mkdir(exist_ok=True)
    h = logging.handlers.RotatingFileHandler(ROOT / "logs" / "media_worker.log", maxBytes=5_000_000, backupCount=2,
                                             encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[h, logging.StreamHandler(sys.stdout)],
                        format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
