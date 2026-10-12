"""LocalAI model downloader.

Reads config/models_manifest.json and downloads only entries in the requested
groups. Files are fetched from Hugging Face (official orgs) or official GitHub
releases and moved into C:/LocalAI/models/<dest>. Already-present files with the
expected size are skipped, so the script is safe to re-run.

Usage:  python download_models.py image tts video
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
STAGING = ROOT / "cache" / "hf" / "staging"
os.environ.setdefault("HF_HOME", str(ROOT / "cache" / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from huggingface_hub import hf_hub_download, snapshot_download  # noqa: E402


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def fetch_url(url, target: Path):
    import httpx

    tmp = target.with_suffix(target.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    os.replace(tmp, target)


def main(groups):
    manifest = json.loads((ROOT / "config" / "models_manifest.json").read_text(encoding="utf-8-sig"))
    entries = [m for m in manifest["models"] if m["group"] in groups or "all" in groups]
    total = sum(m.get("size_gb", 0) for m in entries)
    free = shutil.disk_usage(MODELS).free / 1e9
    log(f"{len(entries)} entries, ~{total:.1f} GB requested, {free:.0f} GB free")
    if total > free - 50:
        log("ERROR: not enough free disk space (keeping 50 GB reserve)")
        sys.exit(2)
    failed = []
    for m in entries:
        for attempt in range(1, 9):   # survive network drops: retry with backoff
            ok = fetch_entry(m)
            if ok:
                break
            wait = min(300, 20 * attempt)
            log(f"retry {attempt}/8 in {wait}s")
            time.sleep(wait)
        else:
            failed.append(m)
    log("DONE" if not failed else f"DONE with {len(failed)} failures")
    sys.exit(1 if failed else 0)


def fetch_entry(m) -> bool:
    dest = MODELS / m["dest"]
    try:
        if m.get("snapshot"):
            dest.mkdir(parents=True, exist_ok=True)
            log(f"snapshot {m['repo']} -> {dest}")
            snapshot_download(m["repo"], local_dir=str(dest), allow_patterns=m.get("allow"))
            log(f"ok  {dest}")
            return True
        target = dest / Path(m.get("rename") or m.get("file") or m["url"].split("/")[-1]).name
        if target.exists() and (not m.get("size_gb") or abs(target.stat().st_size / 1e9 - m["size_gb"]) < 0.05):
            log(f"skip (present) {target.name}")
            return True
        dest.mkdir(parents=True, exist_ok=True)
        log(f"get {m.get('repo', m.get('url'))} :: {m.get('file', '')} ({m.get('size_gb', '?')} GB)")
        if "url" in m:
            fetch_url(m["url"], target)
        else:
            p = hf_hub_download(m["repo"], m["file"], local_dir=str(STAGING / m["repo"].replace("/", "__")))
            os.replace(p, target)
        log(f"ok  {target}")
        return True
    except Exception as e:
        log(f"FAIL {m.get('file') or m.get('repo')}: {str(e)[:300]}")
        return False


if __name__ == "__main__":
    main(sys.argv[1:] or ["image"])
