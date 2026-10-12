"""Photo avatar smoke test: portrait + speech -> LTX-2.5 talking video."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
import comfy  # noqa: E402
import media  # noqa: E402
import workflows  # noqa: E402


async def main():
    img = sys.argv[1]
    audio = sys.argv[2]
    w, h = (int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "768x768").split("x"))
    dur = media.probe(audio)["duration"]
    p = {"width": w, "height": h, "fps": 24, "duration": dur, "seed": 11, "audio_from_model": True, "two_stage": False,
         "prompt": "A young woman with freckles is talking directly to the camera in a calm, friendly way. "
                   "Natural lip movement matching her speech, natural blinking, subtle head movement. "
                   "Soft window light, static camera, photorealistic."}
    g, r = workflows.build_avatar(p, {"image": comfy.stage_input(img), "audio": comfy.stage_input(audio)})
    t = time.time()
    last = [0]

    def cb(f, v, m, n):
        if v is not None and time.time() - last[0] > 5:
            last[0] = time.time()
            print(f"  {time.time() - t:5.0f}s {n} {v}/{m}", flush=True)
    files = await comfy.run(g, cb)
    print(json.dumps({"seconds": round(time.time() - t, 1), **r, "files": [str(f) for f in files]}, indent=1))


asyncio.run(main())

