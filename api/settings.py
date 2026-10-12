"""Central paths, ports and user settings for the LocalAI orchestrator."""
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENVS = ROOT / "envs"
APPS = ROOT / "apps"
MODELS = ROOT / "models"
OUTPUTS = ROOT / "outputs"
OUT_IMAGES = OUTPUTS / "images"
OUT_VIDEOS = OUTPUTS / "videos"
OUT_AUDIO = OUTPUTS / "audio"
# one folder per feature, so results are easy to find
OUT_IMAGE_EDIT = OUTPUTS / "image-edit"
OUT_IMAGE_UPSCALE = OUTPUTS / "image-upscale"
OUT_NOBG = OUTPUTS / "image-no-background"
OUT_VIDEO_EDIT = OUTPUTS / "video-edit"
OUT_VIDEO_UPSCALE = OUTPUTS / "video-upscale"
OUT_INTERPOLATE = OUTPUTS / "video-smooth"
OUT_CLIPS = OUTPUTS / "clips"
OUT_AVATAR = OUTPUTS / "avatar"
OUT_DUBBING = OUTPUTS / "dubbing"
OUT_TRANSLATED = OUTPUTS / "translated-video"
TEMP = ROOT / "temp"
UPLOADS = TEMP / "uploads"
COMFY_IN = TEMP / "comfy_in"
COMFY_OUT = TEMP / "comfy_out"
VOICES = ROOT / "voices"
LOGS = ROOT / "logs"
DATA = ROOT / "data"
CONFIG = ROOT / "config"
UI_DIST = ROOT / "ui" / "dist"
FFMPEG = APPS / "ffmpeg" / "bin" / "ffmpeg.exe"
FFPROBE = APPS / "ffmpeg" / "bin" / "ffprobe.exe"
DB_PATH = DATA / "localai.db"
SETTINGS_PATH = CONFIG / "settings.json"
PIDS_PATH = DATA / "pids.json"

for d in (OUT_IMAGES, OUT_VIDEOS, OUT_AUDIO, OUT_IMAGE_EDIT, OUT_IMAGE_UPSCALE, OUT_NOBG, OUT_VIDEO_EDIT,
          OUT_VIDEO_UPSCALE, OUT_INTERPOLATE, OUT_CLIPS, OUT_AVATAR, OUT_DUBBING, OUT_TRANSLATED, UPLOADS, COMFY_IN, COMFY_OUT, VOICES, LOGS, DATA):
    d.mkdir(parents=True, exist_ok=True)

# Directories whose files may be served to the browser.
SERVABLE_ROOTS = [OUTPUTS, VOICES, UPLOADS, COMFY_IN, DATA / "avatars", DATA / "dub", DATA / "clips"]

DEFAULTS = {
    "vram_mode": "auto",          # auto | keep_loaded | low_vram
    "ports": {"ui": 3000, "api": 8000, "comfyui": 8188,
              "chatterbox": 8101, "qwen": 8102, "omnivoice": 8103, "media": 8104, "llm": 8105, "fish": 8106},
    "open_browser": True,
    "image_default_preset": "balanced",
    "video_default_preset": "balanced",
    "voice_default_preset": "natural",
    "voice_default_engine": "chatterbox",
    "ram_reserve_gb": 8.0,        # keep this much system RAM free before heavy jobs
    "vram_margin_gb": 1.0,
    "youtube_channel": "",        # uploads are locked to this channel name/handle; empty = the one connected channel
}

_lock = threading.Lock()


def load() -> dict:
    data = {}
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    merged = json.loads(json.dumps(DEFAULTS))
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


def save(patch: dict) -> dict:
    with _lock:
        cur = load()
        for k, v in patch.items():
            if k not in DEFAULTS:
                continue
            if isinstance(v, dict) and isinstance(cur.get(k), dict):
                cur[k].update(v)
            else:
                cur[k] = v
        SETTINGS_PATH.write_text(json.dumps(cur, indent=2), encoding="utf-8")
        return cur


def get(key):
    return load().get(key, DEFAULTS.get(key))


# Runtime-resolved ports (may differ from settings if a port was occupied).
RUNTIME_PORTS: dict = {}


def port(name: str) -> int:
    return RUNTIME_PORTS.get(name) or load()["ports"][name]
