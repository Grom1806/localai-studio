"""Direct ComfyUI smoke test for LocalAI workflows. Measures wall time and peak VRAM/RAM.
usage: python smoke_comfy.py image|klein|i2i|video [key=value ...]"""
import asyncio
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
import psutil  # noqa: E402
import pynvml  # noqa: E402

import comfy  # noqa: E402
import workflows  # noqa: E402

pynvml.nvmlInit()
H = pynvml.nvmlDeviceGetHandleByIndex(0)
peak = {"vram": 0, "ram": 0}
stop = False


def sampler():
    while not stop:
        peak["vram"] = max(peak["vram"], pynvml.nvmlDeviceGetMemoryInfo(H).used / 2**30)
        peak["ram"] = max(peak["ram"], psutil.virtual_memory().used / 2**30)
        time.sleep(0.25)


async def main():
    global stop
    kind = sys.argv[1]
    extra = {}
    for a in sys.argv[2:]:
        k, v = a.split("=", 1)
        try:
            v = json.loads(v)
        except Exception:
            pass
        extra[k] = v
    up = {}
    if kind == "image":
        p = {"model": "z_image_turbo", "width": 1024, "height": 1024, "steps": 8, "seed": 42,
             "prompt": "Cinematic photo of an old fisherman mending nets on a wooden pier at golden hour, 85mm, shallow depth of field, film grain"}
        build = workflows.build_image
    elif kind == "klein":
        p = {"model": "flux2_klein_4b", "width": 1024, "height": 1024, "steps": 4, "seed": 42,
             "prompt": "A red vintage bicycle leaning against a pastel blue wall, morning light"}
        build = workflows.build_image
    elif kind == "i2i":
        p = {"model": extra.pop("model", "z_image_turbo"), "width": 1024, "height": 1024, "steps": 8, "seed": 7,
             "mode": "i2i", "denoise": 0.6, "prompt": "Same scene as a snowy winter evening, warm lantern light"}
        up["image"] = comfy.stage_input(extra.pop("image"))
        if p["model"] == "flux2_klein_4b":
            p["steps"] = 4
            p["prompt"] = "Change the scene to a snowy winter evening, keep the subject identical"
        build = workflows.build_image
    else:
        p = {"width": 1024, "height": 576, "duration": 5, "fps": 24, "seed": 42,
             "prompt": "A slow cinematic dolly shot through a rainy neon-lit street at night, reflections on wet asphalt, a woman with an umbrella walks past, soft rain and distant thunder."}
        if "image" in extra:
            up["image"] = comfy.stage_input(extra.pop("image"))
            p["mode"] = "i2v"
        build = workflows.build_video
    p.update(extra)
    graph, resolved = build(p, up)
    base_vram = pynvml.nvmlDeviceGetMemoryInfo(H).used / 2**30
    threading.Thread(target=sampler, daemon=True).start()
    t0 = time.time()
    last = [0]

    def prog(frac, v, m, node):
        if v is not None and time.time() - last[0] > 3:
            last[0] = time.time()
            print(f"  {time.time() - t0:6.1f}s {node} {v}/{m}", flush=True)

    files = await comfy.run(graph, prog, timeout=7200)
    stop = True
    print(json.dumps({"kind": kind, "seconds": round(time.time() - t0, 1), "base_vram_gb": round(base_vram, 2),
                      "peak_vram_gb": round(peak["vram"], 2), "peak_ram_used_gb": round(peak["ram"], 2),
                      "resolved": resolved, "files": [str(f) for f in files]}, indent=1))


asyncio.run(main())
