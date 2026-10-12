"""Russian TTS benchmark - scoring (env-media). Whisper CER + confidence, ECAPA speaker similarity, UTMOS
naturalness, artifacts and duration consistency. usage: ru_bench_score.py engine[,engine...]"""
import json
import re
import sys
from pathlib import Path

import httpx
import jiwer
import numpy as np
import soundfile as sf
import torch
import torchaudio

D = Path(r"C:\LocalAI\data\rubench")
M = "http://127.0.0.1:8104"
dev = "cuda"
import os  # noqa: E402
os.environ.setdefault("HF_HOME", r"C:\LocalAI\cache\hf")
from speechbrain.inference.speaker import EncoderClassifier  # noqa: E402
from speechbrain.utils.fetching import LocalStrategy  # noqa: E402

ecapa = EncoderClassifier.from_hparams("speechbrain/spkrec-ecapa-voxceleb", savedir=r"C:\LocalAI\models\tools\ecapa",
                                       run_opts={"device": dev}, local_strategy=LocalStrategy.COPY)
torch.hub.set_dir(r"C:\LocalAI\models\tools\torch_hub")
utmos = torch.hub.load("tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True).to(dev).eval()


def load16(p):
    x, sr = sf.read(p, dtype="float32", always_2d=True)
    x = torch.from_numpy(x.mean(1))
    return torchaudio.functional.resample(x, sr, 16000) if sr != 16000 else x


def emb(p):
    with torch.no_grad():
        return torch.nn.functional.normalize(ecapa.encode_batch(load16(p)[None].to(dev)).squeeze(), dim=0)


import sys as _s  # noqa: E402
_s.path.insert(0, r"C:\LocalAI\api")
import ru_text  # noqa: E402


def norm(t):
    t = ru_text.normalize(t, overrides={})        # same spoken form for reference and Whisper output
    t = t.lower().replace("ё", "е")
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


ref_emb = emb(D / "ref_ru_human.wav")
summary = {}
for engine in sys.argv[1].split(","):
    meta = json.loads((D / engine / "meta.json").read_text(encoding="utf-8"))
    rows = []
    for r in meta["rows"]:
        wav = D / engine / f"{r['id']}.wav"
        if not r["ok"] or not wav.exists():
            rows.append({**r, "cer": 1.0})
            continue
        tr = httpx.post(M + "/transcribe", json={"path": str(wav), "language": "ru", "vad": False}, timeout=600).json()
        hyp = " ".join(s["text"] for s in tr["segments"])
        probs = [w["prob"] for s in tr["segments"] for w in s["words"]]
        x = load16(wav)
        with torch.no_grad():
            mos = float(utmos(x[None].to(dev), 16000).item())
        sim = float(torch.dot(emb(wav), ref_emb).item())
        a = x.numpy()
        clip = float(np.mean(np.abs(a) > 0.99))
        frame = 1600
        rms = np.array([np.sqrt(np.mean(a[i:i + frame] ** 2)) for i in range(0, len(a) - frame, frame)])
        long_sil = int(np.max(np.convolve(rms < 0.005, np.ones(8), "valid")) >= 8) if len(rms) > 8 else 0
        dur = len(a) / 16000
        rows.append({**r, "hyp": hyp, "cer": round(jiwer.cer(norm(r["text"]), norm(hyp)), 3),
                     "whisper_conf": round(float(np.mean(probs)) if probs else 0, 3), "similarity": round(sim, 3),
                     "utmos": round(mos, 2), "clipping": round(clip, 4), "long_silence": long_sil,
                     "chars_per_s": round(len(norm(r["text"])) / max(dur, 0.1), 1), "rtf": round(r["gen_seconds"] / max(dur, 0.1), 2)})
    ok = [x for x in rows if "similarity" in x]
    cps = np.array([x["chars_per_s"] for x in ok]) if ok else np.array([0])
    summary[engine] = {
        "sentences_ok": f"{len(ok)}/{len(rows)}",
        "CER_%": round(100 * float(np.mean([x["cer"] for x in rows])), 1),
        "whisper_confidence": round(float(np.mean([x["whisper_conf"] for x in ok])), 3) if ok else 0,
        "speaker_similarity": round(float(np.mean([x["similarity"] for x in ok])), 3) if ok else 0,
        "UTMOS": round(float(np.mean([x["utmos"] for x in ok])), 2) if ok else 0,
        "artifacts": int(sum(x["clipping"] > 0.001 or x["long_silence"] for x in ok)),
        "rate_chars_s": round(float(cps.mean()), 1), "rate_variation_%": round(100 * float(cps.std() / max(cps.mean(), 1e-6)), 1),
        "RTF": round(float(np.mean([x["rtf"] for x in ok])), 2) if ok else 0,
        "load_s": meta["load_seconds"], "peak_vram_mb": meta["peak_vram_mb"],
        "worst": sorted(ok, key=lambda x: -x["cer"])[:2] and [
            {"id": x["id"], "cer": x["cer"], "hyp": x["hyp"][:120]} for x in sorted(ok, key=lambda x: -x["cer"])[:2]],
    }
    (D / engine / "scores.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=1))
old = D / "summary.json"
allsum = json.loads(old.read_text(encoding="utf-8")) if old.exists() else {}
allsum.update(summary)
old.write_text(json.dumps(allsum, ensure_ascii=False, indent=1), encoding="utf-8")
