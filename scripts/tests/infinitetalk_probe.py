"""InfiniteTalk diagnostic: one 81-frame chunk per variant, scored by lipsync_score.py.
usage: infinitetalk_probe.py image.png ru.wav en.wav"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"C:\LocalAI\api")
import comfy  # noqa: E402
import media  # noqa: E402
import workflows_tools as wt  # noqa: E402

OUT = Path(r"C:\LocalAI\temp\work\itprobe")
PY = r"C:\LocalAI\envs\env-api\Scripts\python.exe"


def clip16k(src, dst):
    media.run(["-i", src, "-ac", "1", "-ar", 16000, "-t", f"{85 / 25:.3f}", "-af", f"apad=whole_dur={85 / 25:.3f}", dst])


async def variant(name, image, audio, **kw):
    g = wt.build_infinitetalk_chunk({"prompt": kw.get("prompt"), "steps": 6, "seed": 7,
                                     "audio_scale": kw.get("audio_scale", 1.0)},
                                    comfy.stage_input(image), comfy.stage_input(audio), None, 480, 480, 81)
    files = await comfy.run(g, None, timeout=1800)
    vid = next(f for f in files if f.suffix.lower() == ".mp4")
    dst = OUT / f"{name}.mp4"
    # mux the driving audio so the scorer can compare mouth vs loudness
    media.run(["-i", vid, "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-shortest", dst])
    s = json.loads(subprocess.run([PY, r"C:\LocalAI\scripts\tests\lipsync_score.py", str(dst)],
                                  capture_output=True, text=True).stdout)
    print(name, s, flush=True)


def loud(src, dst):
    media.run(["-i", src, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", 16000, "-ac", 1, dst])


async def main(image, ru, en, ru2=None):
    OUT.mkdir(parents=True, exist_ok=True)
    a_ru, a_en = OUT / "ru16k.wav", OUT / "en16k.wav"
    clip16k(ru, a_ru)
    clip16k(en, a_en)
    if ru2 == "job":                          # third round: the avatar job's own first chunk, job prompt vs default
        a = Path(r"C:\LocalAI\temp\comfy_in\8cf3733d9f_a_000.wav")
        job_prompt = ("A person is talking to the camera. "
                      "Natural subtle head movement, natural blinking, relaxed expression.")
        await variant("F_job_default_prompt", image, a)
        await variant("G_job_job_prompt", image, a, prompt=job_prompt)
        return
    if ru2:                                   # second round: loudness + a different Russian voice
        loud(a_ru, OUT / "ru_loud.wav")
        clip16k(ru2, OUT / "ru2_raw.wav")
        loud(OUT / "ru2_raw.wav", OUT / "ru2_loud.wav")
        await variant("D_ru_loud", image, OUT / "ru_loud.wav")
        await variant("E_ru2_loud", image, OUT / "ru2_loud.wav")
        return
    await variant("A_ru", image, a_ru)
    await variant("B_en", image, a_en)
    await variant("C_ru_scale2", image, a_ru, audio_scale=2.0)


asyncio.run(main(*sys.argv[1:5]))
