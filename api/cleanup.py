"""The "Free RAM" and "Clear temporary files" buttons (Dashboard / System).

free_ram(): unloads every model (ComfyUI, voices, media tools, local LLM) and trims the working sets of
LocalAI's own processes, so Windows gets that memory back. Other programs are never touched.

clean_temp(): removes LocalAI scratch data only:
  - temp/ work folders (ComfyUI in/out, job work dirs, test leftovers)
  - interrupted Hugging Face downloads (*.incomplete not written for 60 min)
It keeps temp/uploads (your uploaded files are used by saved projects), compile caches, outputs, models,
voices, avatars and projects. Refused while a job is running.
"""
import ctypes
import gc
import shutil
import time
from pathlib import Path

import psutil

import jobs
import resources
from services import SERVICES
from settings import ROOT, TEMP

KEEP = {"uploads", "torch", "torchinductor_" + (psutil.Process().username().split("\\")[-1])}
HF = ROOT / "cache" / "hf"
STALE_S = 3600


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _temp_items():
    for p in TEMP.iterdir():
        if p.name in KEEP or p.name.startswith("torchinductor_"):
            continue
        yield p


def _stale_partials():
    now = time.time()
    for f in HF.rglob("*.incomplete"):
        try:
            if now - f.stat().st_mtime > STALE_S:
                yield f
        except OSError:
            pass


def _busy() -> bool:
    j = jobs.CURRENT.get("job")
    return bool(j and j.status == "running") or jobs.queue_length() > 0


def temp_info() -> dict:
    t = sum(_size(p) for p in _temp_items() if p.exists())
    h = sum(f.stat().st_size for f in _stale_partials())
    return {"temp_bytes": t, "partial_bytes": h, "total_bytes": t + h, "busy": _busy()}


def clean_temp() -> dict:
    if _busy():
        return {"ok": False, "message": "A job is running; try again when it has finished.", "freed_bytes": 0}
    freed, errors = 0, 0
    for p in list(_temp_items()) + list(_stale_partials()):
        try:
            n = _size(p)
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            freed += n
        except OSError:
            errors += 1                      # file in use: skip it
    for d in ("comfy_in", "comfy_out", "work"):
        (TEMP / d).mkdir(exist_ok=True)      # folders the services expect
    return {"ok": True, "freed_bytes": freed, "skipped": errors}


def _trim(pid: int) -> bool:
    """Ask Windows to page out a process's unused memory (EmptyWorkingSet). Only used on LocalAI processes."""
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    h = k32.OpenProcess(0x0100 | 0x0400, False, pid)      # PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION
    if not h:
        return False
    try:
        return bool(psapi.EmptyWorkingSet(h))
    finally:
        k32.CloseHandle(h)


def _own_pids() -> set[int]:
    pids = {psutil.Process().pid}
    for svc in SERVICES.values():
        pid = svc.pid()
        if not pid:
            continue
        try:
            p = psutil.Process(pid)
            pids.add(pid)
            pids.update(c.pid for c in p.children(recursive=True))
        except psutil.Error:
            pass
    return pids


async def free_ram() -> dict:
    before = psutil.virtual_memory().available
    unloaded = False
    if not _busy():
        await resources.unload_all()
        unloaded = True
    gc.collect()
    trimmed = sum(_trim(pid) for pid in _own_pids())
    after = psutil.virtual_memory().available
    return {"ok": True, "unloaded_models": unloaded, "trimmed_processes": trimmed,
            "freed_bytes": max(0, after - before), "available_bytes": after,
            "total_bytes": psutil.virtual_memory().total,
            "message": None if unloaded else "A job is running, so its models were kept loaded."}
