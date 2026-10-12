"""GPU / RAM / disk telemetry via NVML and psutil (read-only, local)."""
import shutil

import psutil

from settings import ROOT

try:
    import pynvml

    pynvml.nvmlInit()
    _NVML = True
except Exception:
    _NVML = False


def _nvidia_handle():
    """Return the handle of the NVIDIA GPU (the system also has an AMD iGPU)."""
    if not _NVML:
        return None
    for i in range(pynvml.nvmlDeviceGetCount()):
        h = pynvml.nvmlDeviceGetHandleByIndex(i)
        return h
    return None


def gpu_stats() -> dict:
    h = _nvidia_handle()
    if h is None:
        return {"available": False}
    mem = pynvml.nvmlDeviceGetMemoryInfo(h)
    name = pynvml.nvmlDeviceGetName(h)
    if isinstance(name, bytes):
        name = name.decode()
    out = {
        "available": True,
        "name": name,
        "vram_total_mb": round(mem.total / 2**20),
        "vram_used_mb": round(mem.used / 2**20),
        "vram_free_mb": round(mem.free / 2**20),
        "driver": pynvml.nvmlSystemGetDriverVersion(),
    }
    for key, fn in (("temperature_c", lambda: pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)),
                    ("utilization", lambda: pynvml.nvmlDeviceGetUtilizationRates(h).gpu),
                    ("power_w", lambda: round(pynvml.nvmlDeviceGetPowerUsage(h) / 1000)),
                    ("power_limit_w", lambda: round(pynvml.nvmlDeviceGetEnforcedPowerLimit(h) / 1000))):
        try:
            out[key] = fn()
        except Exception:
            out[key] = None
    if isinstance(out["driver"], bytes):
        out["driver"] = out["driver"].decode()
    return out


def vram_free_mb() -> int:
    s = gpu_stats()
    return s.get("vram_free_mb", 0)


def ram_stats() -> dict:
    vm = psutil.virtual_memory()
    return {"ram_total_mb": round(vm.total / 2**20), "ram_used_mb": round(vm.used / 2**20),
            "ram_available_mb": round(vm.available / 2**20), "ram_percent": vm.percent}


def disk_stats() -> dict:
    du = shutil.disk_usage(ROOT)
    return {"disk_total_gb": round(du.total / 1e9), "disk_free_gb": round(du.free / 1e9),
            "disk_used_gb": round(du.used / 1e9), "drive": str(ROOT.drive)}


def cpu_stats() -> dict:
    return {"cpu_percent": psutil.cpu_percent(interval=None)}
