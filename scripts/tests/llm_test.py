"""Test: use the LTX-2.5 Gemma-4 12B text encoder as a chat LLM via ComfyUI's native TextGenerate node."""
import asyncio
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
import comfy  # noqa: E402
from workflows import G  # noqa: E402

PROMPTS = [
    "Translate the following Russian text into natural, spoken German. Output only the translation.\n\n"
    "Привет всем! Сегодня я покажу вам, как за десять минут приготовить идеальную пасту, "
    "и открою один секрет, о котором почти никто не знает.",
    'Rate this podcast excerpt as a short-form social video clip. Answer ONLY with JSON: '
    '{"hook":0-100,"interest":0-100,"context":0-100,"engagement":0-100,"shareability":0-100,"title":"..."}\n\n'
    'Excerpt: "Most people think they need more motivation. They do not. What they need is a system. '
    'Motivation fades after three days, but a system works even when you feel terrible. Here is the one I use."',
]


async def main():
    for prompt in PROMPTS:
        g = G()
        clip = g.add("CLIPLoader", clip_name="gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", type="ltxv",
                     device="default")
        gen = g.add("TextGenerate", clip=G.r(clip), prompt=prompt, max_length=400,
                    **{"sampling_mode": "on", "sampling_mode.temperature": 0.3, "sampling_mode.top_k": 40,
                       "sampling_mode.top_p": 0.9, "sampling_mode.min_p": 0.05,
                       "sampling_mode.repetition_penalty": 1.05, "sampling_mode.seed": 1})
        g.add("PreviewAny", source=G.r(gen))
        t = time.time()
        await comfy.run(g.nodes)
        hist = httpx.get(comfy.base() + "/history?max_items=1").json()
        out = list(hist.values())[-1]["outputs"]
        text = next(v["text"] for v in out.values() if "text" in v)
        print(f"--- {time.time() - t:.1f}s\n{text[0] if isinstance(text, list) else text}\n", flush=True)


asyncio.run(main())
