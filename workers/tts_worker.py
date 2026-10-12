"""LocalAI TTS worker.

One small HTTP service per TTS engine, each started inside its own virtualenv:
    python tts_worker.py --engine chatterbox --port 8101
    python tts_worker.py --engine qwen       --port 8102
    python tts_worker.py --engine omnivoice  --port 8103

Models are loaded lazily on the first request and can be unloaded on demand
(the orchestrator does this when VRAM is needed for image/video work).
Binds to 127.0.0.1 only.
"""
import argparse
import gc
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models" / "tts"
os.environ.setdefault("HF_HOME", str(ROOT / "cache" / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TORCH_HOME", str(ROOT / "cache" / "torch"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

LANG_NAMES = {
    "en": "English", "ru": "Russian", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "pl": "Polish", "tr": "Turkish", "nl": "Dutch", "ar": "Arabic", "hi": "Hindi",
}


class GenRequest(BaseModel):
    text: str
    out_path: str
    language: str = "en"
    ref_audio: str | None = None
    ref_text: str | None = None
    speaker: str | None = None
    instruct: str | None = None
    speed: float = 1.0
    seed: int | None = None
    params: dict = {}


class TranscribeRequest(BaseModel):
    path: str


def split_text(text: str, max_chars: int) -> list[str]:
    """Split long text on sentence boundaries so each chunk stays in the model's comfort zone."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?…。！？])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        while len(s) > max_chars:  # very long sentence: cut on commas / spaces
            cut = max(s.rfind(",", 0, max_chars), s.rfind(" ", 0, max_chars))
            cut = cut if cut > max_chars // 3 else max_chars
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(s[:cut + 1].strip())
            s = s[cut + 1:].strip()
        if len(cur) + len(s) + 1 > max_chars and cur:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


def concat(wavs: list[np.ndarray], sr: int, gap_s: float = 0.12) -> np.ndarray:
    gap = np.zeros(int(sr * gap_s), dtype=np.float32)
    out = []
    for i, w in enumerate(wavs):
        out.append(np.asarray(w, dtype=np.float32).reshape(-1))
        if i < len(wavs) - 1:
            out.append(gap)
    return np.concatenate(out) if out else np.zeros(1, dtype=np.float32)


# ─────────────────────────────── engines ────────────────────────────────
class Engine:
    name = "base"
    languages: list[str] = []
    max_chars = 400
    supports_clone = True

    def __init__(self):
        self.model = None

    def load(self):
        raise NotImplementedError

    def unload(self):
        self.model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()

    def info(self) -> dict:
        return {"languages": self.languages, "speakers": [], "supports_clone": self.supports_clone}

    def generate(self, r: GenRequest) -> tuple[np.ndarray, int, bool]:
        """Returns (audio, sample_rate, speed_applied_natively)."""
        raise NotImplementedError


class ChatterboxEngine(Engine):
    name = "chatterbox"
    languages = ["ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko",
                 "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh"]
    max_chars = 280

    def load(self):
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        ckpt = MODELS / "chatterbox"
        t3 = "t3_mtl23ls_v3.safetensors" if (ckpt / "t3_mtl23ls_v3.safetensors").exists() else None
        self.model = ChatterboxMultilingualTTS.from_local(ckpt, "cuda", t3_model=t3)

    def generate(self, r):
        p = r.params
        kw = dict(
            language_id=r.language,
            exaggeration=float(p.get("exaggeration", 0.5)),
            cfg_weight=float(p.get("cfg_weight", 0.5)),
            temperature=float(p.get("temperature", 0.8)),
            repetition_penalty=float(p.get("repetition_penalty", 1.2)),
            min_p=float(p.get("min_p", 0.05)),
            top_p=float(p.get("top_p", 1.0)),
        )
        wavs = []
        for i, chunk in enumerate(split_text(r.text, self.max_chars)):
            # conditionals are cached on the model after the first chunk
            ref = r.ref_audio if i == 0 else None
            if i == 0 and not ref:
                self.model.conds = None
                self.model = self._with_default_voice()
            w = self.model.generate(chunk, audio_prompt_path=ref, **kw)
            wavs.append(w.squeeze(0).float().cpu().numpy())
        return concat(wavs, self.model.sr), self.model.sr, False

    def _with_default_voice(self):
        from chatterbox.mtl_tts import Conditionals
        conds = MODELS / "chatterbox" / "conds.pt"
        self.model.conds = Conditionals.load(conds, map_location="cuda").to("cuda")
        return self.model


class QwenEngine(Engine):
    """Qwen3-TTS 1.7B. Uses the Base model for cloning and CustomVoice for preset speakers."""
    name = "qwen"
    languages = ["en", "ru", "de", "fr", "es", "it", "pt", "zh", "ja", "ko"]
    max_chars = 600
    SPEAKERS = ["Ryan", "Aiden", "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ono_Anna", "Sohee"]

    def __init__(self):
        super().__init__()
        self.models = {}

    def _get(self, kind):
        if kind not in self.models:
            from qwen_tts import Qwen3TTSModel
            # keep only one of the two Qwen checkpoints resident to save VRAM
            for k in list(self.models):
                del self.models[k]
            super().unload()
            path = MODELS / ("qwen3-tts-1.7b-base" if kind == "base" else "qwen3-tts-1.7b-customvoice")
            self.models[kind] = Qwen3TTSModel.from_pretrained(
                str(path), device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa")
        self.model = self.models[kind]
        return self.model

    def load(self):
        self._get("custom")

    def unload(self):
        self.models.clear()
        super().unload()

    def info(self):
        return {"languages": self.languages, "speakers": self.SPEAKERS, "supports_clone": True}

    def generate(self, r):
        lang = LANG_NAMES.get(r.language, "Auto")
        gen_kw = {}
        for k in ("temperature", "top_p", "top_k", "repetition_penalty"):
            if k in r.params:
                gen_kw[k] = r.params[k]
        chunks = split_text(r.text, self.max_chars)
        if r.ref_audio:
            m = self._get("base")
            prompt = m.create_voice_clone_prompt(
                ref_audio=r.ref_audio, ref_text=r.ref_text or None,
                x_vector_only_mode=not bool(r.ref_text))
            wavs, sr = m.generate_voice_clone(text=chunks, language=[lang] * len(chunks),
                                              voice_clone_prompt=prompt, **gen_kw)
        else:
            m = self._get("custom")
            spk = r.speaker if r.speaker in self.SPEAKERS else "Ryan"
            wavs, sr = m.generate_custom_voice(text=chunks, language=[lang] * len(chunks),
                                               speaker=[spk] * len(chunks),
                                               instruct=[r.instruct or ""] * len(chunks), **gen_kw)
        return concat(list(wavs), sr), sr, False


class OmniVoiceEngine(Engine):
    name = "omnivoice"
    languages = ["en", "ru", "de", "fr", "es", "it", "pt", "zh", "ja", "ko", "pl", "tr", "nl", "ar", "hi", "uk"]
    max_chars = 500

    WHISPER = MODELS / "whisper-large-v3-turbo"

    def load(self):
        from omnivoice import OmniVoice
        kw = {"asr_model_name": str(self.WHISPER)} if self.WHISPER.exists() else {}
        self.model = OmniVoice.from_pretrained(str(MODELS / "omnivoice"), device_map="cuda:0", dtype=torch.float16, **kw)

    def generate(self, r):
        kw = {"num_step": int(r.params.get("num_step", 32)), "speed": float(r.speed or 1.0)}
        if r.ref_audio:
            kw["ref_audio"] = r.ref_audio
            if r.ref_text:
                kw["ref_text"] = r.ref_text
        if r.instruct:
            kw["instruct"] = r.instruct
        wavs = []
        for chunk in split_text(r.text, self.max_chars):
            out = self.model.generate(text=chunk, **kw)
            wavs.append(out[0])
        return concat(wavs, 24000), 24000, True

    def transcribe(self, path: str) -> str:
        """Uses OmniVoice's bundled Whisper ASR to transcribe a reference clip."""
        self.ensure()
        if getattr(self.model, "_asr_pipe", None) is None:
            self.model.load_asr_model(str(self.WHISPER) if self.WHISPER.exists() else None)
        return self.model.transcribe(path)

    def ensure(self):
        if self.model is None:
            self.load()


class FishEngine(Engine):
    """Fish Audio S2 Pro (fish-speech 2.0, Fish Audio Research License: free for non-commercial use)."""
    name = "fish"
    languages = ["ru", "en", "de", "fr", "es", "it", "pt", "zh", "ja", "ko", "pl", "tr", "nl", "uk", "ar"]
    max_chars = 600

    def load(self):
        app = ROOT / "apps" / "fish-speech"
        sys.path.insert(0, str(app))
        from fish_speech.inference_engine import TTSInferenceEngine
        from fish_speech.models.dac.inference import load_model as load_decoder
        from fish_speech.models.text2semantic import inference as t2s
        from fish_speech.models.text2semantic.inference import launch_thread_safe_queue
        ck = MODELS / "fish-s2-pro"
        # The checkpoint declares max_seq_len 32768 and fish-speech pre-allocates the KV cache for all of it
        # (~19 GB total, more than a 16 GB card). One request needs < 3k tokens (600 chars + reference +
        # max_new_tokens 1024), so cap the cache at 8192 tokens.
        if not getattr(t2s.init_model, "_localai_capped", False):
            orig_init = t2s.init_model

            def capped_init(*a, **kw):
                model, decode = orig_init(*a, **kw)
                model.config.max_seq_len = min(model.config.max_seq_len, 8192)
                return model, decode
            capped_init._localai_capped = True
            t2s.init_model = capped_init
        queue = launch_thread_safe_queue(checkpoint_path=str(ck), device="cuda", precision=torch.bfloat16,
                                         compile=False)
        self.queue = queue          # the model lives in fish-speech's worker thread; unload() must stop it
        decoder = load_decoder(config_name="modded_dac_vq", checkpoint_path=str(ck / "codec.pth"), device="cuda")
        self.model = TTSInferenceEngine(llama_queue=queue, decoder_model=decoder, precision=torch.bfloat16,
                                        compile=False)
        self.sr = decoder.sample_rate

    def generate(self, r):
        from fish_speech.utils.schema import ServeReferenceAudio, ServeTTSRequest
        refs = []
        if r.ref_audio:
            refs = [ServeReferenceAudio(audio=Path(r.ref_audio).read_bytes(), text=r.ref_text or "")]
        p = r.params
        wavs = []
        for chunk in split_text(r.text, self.max_chars):
            req = ServeTTSRequest(text=chunk, references=refs, max_new_tokens=int(p.get("max_new_tokens", 1024)),
                                  chunk_length=200, top_p=float(p.get("top_p", 0.8)),
                                  repetition_penalty=float(p.get("repetition_penalty", 1.1)),
                                  temperature=float(p.get("temperature", 0.8)), format="wav",
                                  seed=r.seed if r.seed is not None and r.seed >= 0 else None)
            audio = None
            for res in self.model.inference(req):
                if res.code == "error":
                    raise RuntimeError(str(res.error))
                if res.code == "final" and isinstance(res.audio, tuple):
                    self.sr, audio = res.audio
            if audio is None:
                raise RuntimeError("Fish S2 returned no audio")
            wavs.append(np.asarray(audio, dtype=np.float32).reshape(-1))
        return concat(wavs, self.sr), self.sr, False

    def unload(self):
        # Stop fish-speech's model thread (None ends its loop); otherwise the model stays in VRAM and the
        # next load() would allocate a second copy.
        q = getattr(self, "queue", None)
        if q is not None:
            before = threading.active_count()
            q.put(None)
            for _ in range(100):
                if threading.active_count() < before:
                    break
                time.sleep(0.1)
        self.queue = None
        self.model = None
        super().unload()


ENGINES = {"chatterbox": ChatterboxEngine, "qwen": QwenEngine, "omnivoice": OmniVoiceEngine, "fish": FishEngine}

# ─────────────────────────────── service ────────────────────────────────
args_ns = None
engine: Engine = None
state = {"status": "idle", "error": None, "last_used": 0.0}
lock = threading.Lock()
app = FastAPI()
log = logging.getLogger("tts")


def ensure_loaded():
    if engine.model is None and not getattr(engine, "models", None):
        state["status"] = "loading"
        t = time.time()
        engine.load()
        log.info("model loaded in %.1fs", time.time() - t)
    state["status"] = "ready"


def vram_mb():
    if not torch.cuda.is_available():
        return 0
    return round(torch.cuda.memory_reserved() / 2**20)


@app.get("/health")
def health():
    loaded = engine.model is not None or bool(getattr(engine, "models", None))
    return {"engine": engine.name, "status": state["status"], "loaded": loaded,
            "vram_mb": vram_mb(), "error": state["error"], "pid": os.getpid()}


@app.get("/info")
def info():
    return {"engine": engine.name, **engine.info()}


@app.post("/load")
def load():
    with lock:
        try:
            ensure_loaded()
            return health()
        except Exception as e:
            log.error("load failed\n%s", traceback.format_exc())
            state.update(status="error", error=str(e))
            return JSONResponse({"error": f"Model failed to load: {e}"}, status_code=500)


@app.post("/unload")
def unload():
    with lock:
        engine.unload()
        state["status"] = "idle"
        return health()


@app.post("/generate")
def generate(r: GenRequest):
    with lock:
        t0 = time.time()
        try:
            ensure_loaded()
            state["status"] = "busy"
            if r.seed is not None and r.seed >= 0:
                torch.manual_seed(r.seed)
                np.random.seed(r.seed % (2**32))
            with torch.inference_mode():
                audio, sr, speed_done = engine.generate(r)
            audio = np.clip(np.asarray(audio, dtype=np.float32), -1, 1)
            Path(r.out_path).parent.mkdir(parents=True, exist_ok=True)
            sf.write(r.out_path, audio, sr, subtype="PCM_16")
            state.update(status="ready", error=None, last_used=time.time())
            return {"path": r.out_path, "sample_rate": sr, "duration": len(audio) / sr,
                    "seconds": round(time.time() - t0, 2), "speed_applied": speed_done,
                    "peak_vram_mb": round(torch.cuda.max_memory_reserved() / 2**20)}
        except torch.cuda.OutOfMemoryError as e:
            log.error("OOM\n%s", traceback.format_exc())
            engine.unload()
            state.update(status="idle", error="out of memory")
            return JSONResponse({"error": "Not enough VRAM for voice generation.", "code": "oom",
                                 "detail": str(e)}, status_code=507)
        except Exception as e:
            log.error("generate failed\n%s", traceback.format_exc())
            state.update(status="ready" if engine.model is not None else "error", error=str(e))
            return JSONResponse({"error": str(e), "code": "failed"}, status_code=500)


@app.post("/transcribe")
def transcribe(r: TranscribeRequest):
    if not hasattr(engine, "transcribe"):
        return JSONResponse({"error": "not supported"}, status_code=400)
    with lock:
        try:
            return {"text": engine.transcribe(r.path)}
        except Exception as e:
            log.error("transcribe failed\n%s", traceback.format_exc())
            return JSONResponse({"error": str(e)}, status_code=500)


def main():
    global engine, args_ns
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, choices=ENGINES)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--preload", action="store_true")
    args_ns = ap.parse_args()
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_dir / f"tts_{args_ns.engine}.log",
                                                   maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler(sys.stdout)],
                        format="%(asctime)s %(levelname)s %(message)s")
    engine = ENGINES[args_ns.engine]()
    if args_ns.preload:
        ensure_loaded()
    uvicorn.run(app, host="127.0.0.1", port=args_ns.port, log_level="warning")


if __name__ == "__main__":
    main()
