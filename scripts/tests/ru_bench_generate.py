"""Russian TTS benchmark - generation. Same text (normalized), same human reference voice, same settings.
usage: ru_bench_generate.py engine[,engine...]"""
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pynvml

sys.path.insert(0, r"C:\LocalAI\api")
import ru_text  # noqa: E402

D = Path(r"C:\LocalAI\data\rubench")
PORTS = {"chatterbox": 8101, "qwen": 8102, "omnivoice": 8103, "fish": 8106}
REF = str(D / "ref_ru_human.wav")
REF_TEXT = (D / "ref_ru_human.txt").read_text(encoding="utf-8")
SET = json.loads(Path(r"C:\LocalAI\scripts\tests\ru_benchmark_set.json").read_text(encoding="utf-8"))
PARAMS = {"chatterbox": {"cfg_weight": 0.5, "exaggeration": 0.5, "temperature": 0.8}, "qwen": {},
          "omnivoice": {"num_step": 32}, "fish": {}}
pynvml.nvmlInit()
H = pynvml.nvmlDeviceGetHandleByIndex(0)

for engine in sys.argv[1].split(","):
    port = PORTS[engine]
    out_dir = D / engine
    out_dir.mkdir(exist_ok=True)
    for other, p in PORTS.items():                 # only one engine on the GPU at a time
        try:
            httpx.post(f"http://127.0.0.1:{p}/unload", timeout=60)
        except Exception:
            pass
    time.sleep(2)
    base = pynvml.nvmlDeviceGetMemoryInfo(H).used / 2**20
    peak = {"v": base}
    stop = False

    def sampler():
        while not stop:
            peak["v"] = max(peak["v"], pynvml.nvmlDeviceGetMemoryInfo(H).used / 2**20)
            time.sleep(0.2)
    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    t_load = time.time()
    httpx.post(f"http://127.0.0.1:{port}/load", timeout=900)
    load_s = time.time() - t_load
    rows = []
    for s in SET:
        text = ru_text.normalize(s["text"])
        body = {"text": text, "out_path": str(out_dir / f"{s['id']}.wav"), "language": "ru", "ref_audio": REF,
                "ref_text": REF_TEXT, "params": PARAMS[engine], "seed": 1234}
        t0 = time.time()
        r = httpx.post(f"http://127.0.0.1:{port}/generate", json=body, timeout=900)
        dt = time.time() - t0
        d = r.json()
        rows.append({"id": s["id"], "cat": s["cat"], "text": text, "ok": r.status_code == 200,
                     "gen_seconds": round(dt, 2), "audio_seconds": d.get("duration"), "error": d.get("error")})
        print(engine, s["id"], r.status_code, round(dt, 1), d.get("duration"), d.get("error", ""), flush=True)
    stop = True
    th.join()
    meta = {"engine": engine, "load_seconds": round(load_s, 1), "peak_vram_mb": round(peak["v"] - base),
            "rows": rows}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    httpx.post(f"http://127.0.0.1:{port}/unload", timeout=60)
