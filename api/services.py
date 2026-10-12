"""Background service manager: starts/stops/health-checks ComfyUI and TTS workers.

All services bind to 127.0.0.1, run without console windows and log to logs/.
If a configured port is busy, the manager reuses it when the expected service is
already answering there, otherwise it picks the next free port.
"""
import asyncio
import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx

import settings
from settings import APPS, CONFIG, COMFY_IN, COMFY_OUT, ENVS, LOGS, PIDS_PATH, ROOT, TEMP

log = logging.getLogger("services")
CREATE_NO_WINDOW = 0x08000000
LOG_MAX_BYTES = 5_000_000


def _child_env():
    env = os.environ.copy()
    env.update({
        "HF_HOME": str(ROOT / "cache" / "hf"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_OFFLINE": "1",          # all models are local; backends never reach out at load time
        "TRANSFORMERS_OFFLINE": "1",
        "DO_NOT_TRACK": "1",
        "TORCH_HOME": str(ROOT / "cache" / "torch"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "TEMP": str(TEMP), "TMP": str(TEMP),
        "PATH": str(APPS / "ffmpeg" / "bin") + os.pathsep + env.get("PATH", ""),
    })
    return env


class Service:
    def __init__(self, name, label, kind, env, args, health_path, port_key, startup_timeout=180, exe=None,
                 autostart=True, requires=None):
        self.name, self.label, self.kind = name, label, kind
        self.env_name, self.args, self.health_path = env, args, health_path
        self.port_key = port_key
        self.startup_timeout = startup_timeout
        self.exe = Path(exe) if exe else None     # non-Python services (llama-server)
        self.autostart = autostart                # False = started on demand, stopped to free VRAM
        self.requires = [Path(r) for r in (requires or [])]
        self.proc: subprocess.Popen | None = None
        self.adopted_pid: int | None = None
        self.status = "offline"          # offline | starting | online | error
        self.error: str | None = None
        self.health: dict = {}
        self.started_at = 0.0

    @property
    def python(self) -> Path:
        return ENVS / self.env_name / "Scripts" / "python.exe"

    @property
    def port(self) -> int:
        return settings.port(self.port_key)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def installed(self) -> bool:
        return (self.exe or self.python).exists() and all(r.exists() for r in self.requires)

    @property
    def log_path(self) -> Path:
        return LOGS / f"{self.name}.console.log"

    def pid(self):
        if self.proc and self.proc.poll() is None:
            return self.proc.pid
        return self.adopted_pid

    def to_dict(self):
        return {"name": self.name, "label": self.label, "kind": self.kind, "status": self.status,
                "port": self.port, "pid": self.pid(), "error": self.error, "installed": self.installed,
                "health": self.health, "uptime_s": round(time.time() - self.started_at) if self.started_at else 0}

    async def check(self, timeout=2.0) -> bool:
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(self.url + self.health_path)
            if r.status_code == 200:
                self.health = r.json() if "json" in r.headers.get("content-type", "") else {}
                if not self._identity_ok():
                    return False
                if self.status != "online":
                    log.info("%s online on port %s", self.name, self.port)
                self.status, self.error = "online", None
                return True
        except Exception:
            pass
        if self.proc is not None and self.proc.poll() is not None:
            self.status = "error"
            self.error = f"process exited with code {self.proc.returncode} (see logs/{self.log_path.name})"
            self.proc = None
        elif self.status == "online":
            self.status = "offline"
        return False

    def _identity_ok(self):
        if self.kind in ("tts", "media"):
            return self.health.get("engine") == self.name
        if self.kind == "llm":
            return self.health.get("status") == "ok"
        if self.name == "comfyui":
            return "system" in self.health
        return True

    def _rotate_log(self):
        p = self.log_path
        if p.exists() and p.stat().st_size > LOG_MAX_BYTES:
            old = p.with_suffix(".log.1")
            old.unlink(missing_ok=True)
            p.rename(old)

    async def start(self):
        if not self.installed:
            self.status, self.error = "error", "not installed"
            return
        if await self.check():
            return  # already running (possibly started by an earlier session): reuse
        if _port_busy(self.port):
            new = _free_port(self.port + 1)
            log.warning("%s: port %s busy with another program, using %s", self.name, self.port, new)
            settings.RUNTIME_PORTS[self.port_key] = new
        self._rotate_log()
        args = [str(self.exe or self.python)] + [a.format(port=self.port) for a in self.args]
        log.info("starting %s: %s", self.name, " ".join(args))
        out = open(self.log_path, "ab")
        env = _child_env()
        if self.name == "media":        # may fetch the small gated diarization model once (after license acceptance)
            env.pop("HF_HUB_OFFLINE", None)
            env.pop("TRANSFORMERS_OFFLINE", None)
        self.proc = subprocess.Popen(args, cwd=str(ROOT), env=env, stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        self.adopted_pid = None
        self.status, self.error, self.started_at = "starting", None, time.time()
        _save_pids()

    async def wait_online(self, timeout=None) -> bool:
        deadline = time.time() + (timeout or self.startup_timeout)
        while time.time() < deadline:
            if await self.check():
                return True
            if self.status == "error":
                return False
            await asyncio.sleep(1.0)
        self.status, self.error = "error", "did not become healthy in time"
        return False

    async def stop(self):
        pid = self.pid()
        if not pid and await self.check():
            pid = _pid_on_port(self.port)
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
        self.proc, self.adopted_pid = None, None
        self.status, self.health, self.started_at = "offline", {}, 0
        _save_pids()

    async def restart(self):
        await self.stop()
        await asyncio.sleep(1.5)
        await self.start()
        return await self.wait_online()


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _free_port(start: int) -> int:
    for p in range(start, start + 50):
        if not _port_busy(p):
            return p
    raise RuntimeError("no free port found")


def _pid_on_port(port: int):
    try:
        import psutil
        for c in psutil.net_connections("tcp"):
            if c.laddr and c.laddr.port == port and c.status == "LISTEN":
                return c.pid
    except Exception:
        return None


def _comfy_flags():
    p = CONFIG / "comfyui_flags.json"
    try:
        return json.loads(p.read_text()) if p.exists() else []
    except Exception:
        return []


def comfy_args():
    return [
        str(APPS / "ComfyUI" / "main.py"),
        "--listen", "127.0.0.1", "--port", "{port}",
        "--extra-model-paths-config", str(CONFIG / "extra_model_paths.yaml"),
        "--output-directory", str(COMFY_OUT),
        "--input-directory", str(COMFY_IN),
        "--temp-directory", str(TEMP),
        "--user-directory", str(ROOT / "data" / "comfy_user"),
        "--disable-auto-launch",
        "--preview-method", "auto",
        *_comfy_flags(),
    ]


def _tts(name, label, env, port_key):
    return Service(name, label, "tts", env,
                   [str(ROOT / "workers" / "tts_worker.py"), "--engine", name, "--port", "{port}"],
                   "/health", port_key, startup_timeout=120)


SERVICES: dict[str, Service] = {
    "comfyui": Service("comfyui", "ComfyUI", "comfyui", "env-comfyui", comfy_args(), "/system_stats",
                       "comfyui", startup_timeout=240),
    "chatterbox": _tts("chatterbox", "Chatterbox Multilingual", "env-chatterbox", "chatterbox"),
    "qwen": _tts("qwen", "Qwen3-TTS", "env-qwen", "qwen"),
    "omnivoice": _tts("omnivoice", "OmniVoice", "env-omnivoice", "omnivoice"),
    "fish": _tts("fish", "Fish Audio S2 Pro", "env-fish", "fish"),   # requires set below
    "media": Service("media", "Media tools (Whisper, Demucs, tracking)", "media", "env-media",
                     [str(ROOT / "workers" / "media_worker.py"), "--port", "{port}"], "/health", "media",
                     startup_timeout=120),
    # Local multimodal LLM for translation, clip scoring, video review and social metadata.
    "llm": Service("llm", "Local LLM (Gemma-4 12B)", "llm", None,
                    ["-m", str(ROOT / "models" / "llm" / "gemma-4-12b-it-qat-q4_0.gguf"), "--host", "127.0.0.1",
                    "--mmproj", str(ROOT / "models" / "llm" / "mmproj-gemma-4-12b-it-qat-q4_0.gguf"),
                    "--image-max-tokens", "560", "--port", "{port}", "-ngl", "99", "-c", "16384", "-fa", "on", "--no-webui", "-np", "1", "--reasoning-budget", "0"],
                   "/health", "llm", startup_timeout=180, exe=APPS / "llama.cpp" / "llama-server.exe",
                   autostart=False, requires=[ROOT / "models" / "llm" / "gemma-4-12b-it-qat-q4_0.gguf",
                                               ROOT / "models" / "llm" / "mmproj-gemma-4-12b-it-qat-q4_0.gguf"]),
}


def _save_pids():
    data = {n: s.pid() for n, s in SERVICES.items() if s.pid()}
    try:
        PIDS_PATH.write_text(json.dumps(data))
    except Exception:
        pass


def adopt_from_pidfile():
    try:
        data = json.loads(PIDS_PATH.read_text())
    except Exception:
        return
    import psutil
    for n, pid in data.items():
        if n in SERVICES and psutil.pid_exists(pid):
            SERVICES[n].adopted_pid = pid


async def start_all():
    adopt_from_pidfile()
    for s in SERVICES.values():
        if not s.autostart:
            continue
        if not s.installed:          # e.g. an engine whose install was paused: show it as not installed
            s.status, s.error = "offline", "not installed"
            continue
        try:
            await s.start()
        except Exception as e:
            s.status, s.error = "error", str(e)
            log.exception("failed to start %s", s.name)


async def monitor_loop():
    while True:
        for s in SERVICES.values():
            if s.status != "offline" or s.pid():
                await s.check()
        await asyncio.sleep(3)


async def stop_all():
    for s in SERVICES.values():
        await s.stop()

SERVICES["fish"].requires = [ENVS / "env-fish" / "Lib" / "site-packages" / "torch", ROOT / "apps" / "fish-speech" / "fish_speech",
                             ROOT / "models" / "tts" / "fish-s2-pro" / "codec.pth"]
