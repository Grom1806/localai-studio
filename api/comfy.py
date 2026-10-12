"""Async ComfyUI client: upload, queue, live progress over websocket, outputs, memory control."""
import asyncio
import json
import shutil
import uuid
from pathlib import Path

import httpx
import websockets

import settings
from settings import COMFY_IN, COMFY_OUT


class ComfyError(Exception):
    def __init__(self, message, detail=None, node_type=None):
        super().__init__(message)
        self.detail = detail or message
        self.node_type = node_type


def base():
    return f"http://127.0.0.1:{settings.port('comfyui')}"


async def online() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            return (await c.get(base() + "/system_stats")).status_code == 200
    except Exception:
        return False


def stage_input(path: str | Path) -> str:
    """Copy a local file into ComfyUI's input folder (hardlink when possible) and return its name."""
    src = Path(path)
    name = f"{uuid.uuid4().hex[:10]}_{src.name}"
    dst = COMFY_IN / name
    try:
        dst.hardlink_to(src)
    except Exception:
        shutil.copy2(src, dst)
    return name


async def free(unload_models=True):
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            await c.post(base() + "/free", json={"unload_models": unload_models, "free_memory": True})
    except Exception:
        pass


async def interrupt():
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(base() + "/interrupt")
    except Exception:
        pass


async def object_info(node: str | None = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(base() + ("/object_info/" + node if node else "/object_info"))
        return r.json()


async def models_in_vram() -> list:
    """Best effort: ComfyUI reports device memory; per-model info is not exposed by the API."""
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            return (await c.get(base() + "/system_stats")).json().get("devices", [])
    except Exception:
        return []


async def run(graph: dict, on_progress=None, timeout=3600) -> list[Path]:
    """Queue a prompt, stream progress, return list of output file paths (in COMFY_OUT)."""
    client_id = uuid.uuid4().hex
    ws_url = base().replace("http", "ws") + f"/ws?clientId={client_id}"
    total_nodes = len(graph)
    done_nodes = set()
    async with websockets.connect(ws_url, max_size=2**26, open_timeout=10) as ws:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(base() + "/prompt", json={"prompt": graph, "client_id": client_id})
            if r.status_code != 200:
                data = r.json() if "json" in r.headers.get("content-type", "") else {"error": r.text}
                raise ComfyError(_validation_message(data), detail=json.dumps(data)[:4000])
            prompt_id = r.json()["prompt_id"]

        async def reader():
            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    continue  # binary preview frames
                m = json.loads(msg)
                t, d = m.get("type"), m.get("data", {})
                if d.get("prompt_id") not in (None, prompt_id):
                    continue
                if t == "progress" and on_progress:
                    node = graph.get(str(d.get("node")), {}).get("class_type", "")
                    on_progress(len(done_nodes) / max(total_nodes, 1), d["value"], d["max"], node)
                elif t == "executing":
                    if d.get("node") is None:
                        return
                    done_nodes.add(d["node"])
                    if on_progress:
                        node = graph.get(str(d["node"]), {}).get("class_type", "")
                        on_progress(len(done_nodes) / max(total_nodes, 1), None, None, node)
                elif t == "execution_success":
                    return
                elif t == "execution_error":
                    raise ComfyError(d.get("exception_message", "execution error"),
                                     detail="\n".join(d.get("traceback", []))[-6000:],
                                     node_type=d.get("node_type"))
                elif t == "execution_interrupted":
                    raise ComfyError("Generation was cancelled.")

        await asyncio.wait_for(reader(), timeout=timeout)

    async with httpx.AsyncClient(timeout=60) as c:
        hist = (await c.get(base() + f"/history/{prompt_id}")).json().get(prompt_id, {})
    files = []
    for node_out in hist.get("outputs", {}).values():
        for items in node_out.values():
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict) and it.get("filename") and it.get("type", "output") == "output":
                    files.append(COMFY_OUT / it.get("subfolder", "") / it["filename"])
    return files


def _validation_message(data: dict) -> str:
    err = data.get("error", {})
    msg = err.get("message", "") if isinstance(err, dict) else str(err)
    for node in (data.get("node_errors") or {}).values():
        for e in node.get("errors", []):
            det = e.get("details", "")
            if "not in list" in det or "value_not_in_list" in e.get("type", ""):
                return f"A required model file is missing: {det}"
            msg = f"{msg}: {e.get('message')} {det}".strip()
    return msg or "ComfyUI rejected the workflow"
