"""Validate LocalAI workflow graphs against a running ComfyUI's /object_info (node names, inputs, output slots)."""
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
import workflows  # noqa: E402

info = httpx.get("http://127.0.0.1:8188/object_info", timeout=60).json()


def check(name, graph):
    errs = []
    for nid, n in graph.items():
        cls = n["class_type"]
        if cls not in info:
            errs.append(f"[{nid}] unknown node {cls}")
            continue
        spec = info[cls]["input"]
        allowed = {**spec.get("required", {}), **spec.get("optional", {})}
        for k, v in n["inputs"].items():
            if k not in allowed:
                errs.append(f"[{nid}] {cls}: unknown input '{k}' (allowed: {list(allowed)})")
                continue
            t = allowed[k][0]
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                src = graph[v[0]]["class_type"]
                outs = info.get(src, {}).get("output", [])
                if v[1] >= len(outs):
                    errs.append(f"[{nid}] {cls}.{k}: source {src} has no output slot {v[1]}")
            elif isinstance(t, list) and v not in t:
                errs.append(f"[{nid}] {cls}.{k}: value {v!r} not in options (first: {t[:6]})")
        for k in spec.get("required", {}):
            if k not in n["inputs"]:
                errs.append(f"[{nid}] {cls}: missing required input '{k}'")
    print(f"== {name}: {'OK' if not errs else f'{len(errs)} problems'}")
    for e in errs:
        print("   ", e)


check("z-image t2i", workflows.build_image({"model": "z_image_turbo", "width": 1024, "height": 1024, "steps": 8, "prompt": "x"}, {})[0])
check("z-image i2i+inpaint+lora+upscale", workflows.build_image(
    {"model": "z_image_turbo", "width": 1024, "height": 1024, "steps": 8, "prompt": "x", "mode": "inpaint", "upscale": True,
     "negative": "bad", "cfg": 2}, {"image": "a.png", "mask": "m.png"})[0])
check("klein edit", workflows.build_image({"model": "flux2_klein_4b", "width": 1024, "height": 1024, "steps": 4, "prompt": "x", "upscale": True},
                                          {"image": "a.png", "refs": ["b.png"]})[0])
check("ltx t2v 2-stage", workflows.build_video({"width": 1024, "height": 576, "duration": 5, "fps": 24, "prompt": "x"}, {})[0])
check("ltx i2v 1-stage", workflows.build_video({"width": 768, "height": 448, "duration": 4, "fps": 24, "prompt": "x", "mode": "i2v",
                                                "two_stage": False, "audio": False}, {"image": "a.png"})[0])
for n in ("LTXVCropGuides", "LTXVSeparateAVLatent", "LTXVConditioning", "SaveVideo", "CreateVideo", "ImageScaleToTotalPixels"):
    i = info.get(n, {})
    print(n, "outputs:", i.get("output"), "| inputs:", {k: (v[0] if not isinstance(v[0], list) else v[0][:5]) for k, v in {**i.get("input", {}).get("required", {}), **i.get("input", {}).get("optional", {})}.items()})
