"""ComfyUI API-format workflow builders (based on the official ComfyUI templates).

Image:  Z-Image-Turbo (text-to-image, image-to-image, inpaint, LoRA, upscale/detail pass)
        FLUX.2 [klein] 4B (text-to-image and reference-image editing)
Video:  LTX-2.5 22B distilled nvfp4 (text-to-video / image-to-video with joint audio),
        two-stage pipeline: half-res generation -> x2 latent upscale -> 3-step refine.
"""
import random


class G:
    """Tiny graph builder: add() returns a node id; ref(id, slot) makes a link."""

    def __init__(self):
        self.nodes, self.n = {}, 0

    def add(self, cls, **inputs):
        self.n += 1
        nid = str(self.n)
        self.nodes[nid] = {"class_type": cls, "inputs": inputs}
        return nid

    @staticmethod
    def r(nid, slot=0):
        return [nid, slot]


def seed_or_random(seed):
    if seed is None or int(seed) < 0:
        return random.randint(0, 2**50)
    return int(seed)


IMAGE_MODELS = {
    "z_image_turbo": {
        "label": "Z-Image Turbo (photoreal)",
        "unet": "z_image_turbo_bf16.safetensors",
        "clip": "qwen_3_4b.safetensors", "clip_type": "lumina2",
        "vae": "ae.safetensors",
        "supports": ["t2i", "i2i", "inpaint", "lora", "negative"],
    },
    "flux2_klein_4b": {
        "label": "FLUX.2 klein 4B (edit / reference)",
        "unet": "flux-2-klein-4b.safetensors",
        "clip": "qwen_3_4b.safetensors", "clip_type": "flux2",
        "vae": "flux2-vae.safetensors",
        "supports": ["t2i", "edit", "lora"],
    },
}

UPSCALER = "RealESRGAN_x4plus.pth"


def _upscale_detail(g, image, model, pos, neg, vae, seed, factor=2.0, detail=0.3, steps=6, sampler="res_multistep",
                    scheduler="simple"):
    """RealESRGAN x4 -> downscale to `factor`, then optional low-denoise re-sample for detail/face enhancement."""
    up_model = g.add("UpscaleModelLoader", model_name=UPSCALER)
    up = g.add("ImageUpscaleWithModel", upscale_model=G.r(up_model), image=image)
    img = G.r(g.add("ImageScaleBy", image=G.r(up), upscale_method="lanczos", scale_by=factor / 4.0))
    if detail and detail > 0 and model is not None:
        lat = g.add("VAEEncodeTiled", pixels=img, vae=vae, tile_size=1024, overlap=64, temporal_size=64,
                    temporal_overlap=8)
        ks = g.add("KSampler", model=model, positive=pos, negative=neg, latent_image=G.r(lat), seed=seed,
                   steps=steps, cfg=1.0, sampler_name=sampler, scheduler=scheduler, denoise=float(detail))
        img = G.r(g.add("VAEDecodeTiled", samples=G.r(ks), vae=vae, tile_size=1024, overlap=64, temporal_size=64,
                        temporal_overlap=8))
    return img


def build_image(p: dict, uploaded: dict) -> tuple[dict, dict]:
    """p: request params (already merged with preset). uploaded: {'image': name, 'mask': name, 'refs': [names]}
    Returns (graph, resolved_params)."""
    model_key = p.get("model") or "z_image_turbo"
    m = IMAGE_MODELS[model_key]
    g = G()
    seed = seed_or_random(p.get("seed"))
    w, h = int(p["width"]) // 16 * 16, int(p["height"]) // 16 * 16
    steps = int(p["steps"])
    cfg = float(p.get("cfg", 1.0))
    mode = p.get("mode", "t2i")
    tiled = bool(p.get("tiled_vae")) or w * h > 1600 * 1600

    unet = g.add("UNETLoader", unet_name=m["unet"], weight_dtype="default")
    clip = g.add("CLIPLoader", clip_name=m["clip"], type=m["clip_type"], device="default")
    vae = G.r(g.add("VAELoader", vae_name=m["vae"]))
    model = G.r(unet)
    if p.get("lora"):
        model = G.r(g.add("LoraLoaderModelOnly", model=model, lora_name=p["lora"],
                          strength_model=float(p.get("lora_strength", 0.8))))

    pos = G.r(g.add("CLIPTextEncode", clip=G.r(clip), text=p.get("prompt", "")))

    def decode(samples):
        if tiled:
            return G.r(g.add("VAEDecodeTiled", samples=samples, vae=vae, tile_size=1024, overlap=64,
                             temporal_size=64, temporal_overlap=8))
        return G.r(g.add("VAEDecode", samples=samples, vae=vae))

    if model_key == "flux2_klein_4b":
        neg = G.r(g.add("ConditioningZeroOut", conditioning=pos))
        refs = list(uploaded.get("refs") or [])
        if uploaded.get("image"):
            refs.insert(0, uploaded["image"])
        for name in refs[:4]:
            li = g.add("LoadImage", image=name)
            sc = g.add("ImageScaleToTotalPixels", image=G.r(li), upscale_method="lanczos", megapixels=1.0,
                       resolution_steps=1)
            enc = G.r(g.add("VAEEncode", pixels=G.r(sc), vae=vae))
            pos = G.r(g.add("ReferenceLatent", conditioning=pos, latent=enc))
            neg = G.r(g.add("ReferenceLatent", conditioning=neg, latent=enc))
        latent = G.r(g.add("EmptyFlux2LatentImage", width=w, height=h, batch_size=1))
        sig = g.add("Flux2Scheduler", steps=steps, width=w, height=h)
        noise = g.add("RandomNoise", noise_seed=seed)
        sampler = g.add("KSamplerSelect", sampler_name=p.get("sampler") or "euler")
        guider = g.add("CFGGuider", model=model, positive=pos, negative=neg, cfg=cfg)
        out = g.add("SamplerCustomAdvanced", noise=G.r(noise), guider=G.r(guider), sampler=G.r(sampler),
                    sigmas=G.r(sig), latent_image=latent)
        img = decode(G.r(out))
        detail_model, detail_sampler = None, None
    else:
        model = G.r(g.add("ModelSamplingAuraFlow", model=model, shift=float(p.get("shift", 3.0))))
        if p.get("negative") and cfg > 1.0:
            neg = G.r(g.add("CLIPTextEncode", clip=G.r(clip), text=p["negative"]))
        else:
            neg = G.r(g.add("ConditioningZeroOut", conditioning=pos))
        denoise = 1.0
        if mode in ("i2i", "inpaint") and uploaded.get("image"):
            li = g.add("LoadImage", image=uploaded["image"])
            sc = g.add("ImageScale", image=G.r(li), upscale_method="lanczos", width=w, height=h, crop="center")
            latent = G.r(g.add("VAEEncode", pixels=G.r(sc), vae=vae))
            denoise = float(p.get("denoise", 0.6))
            if mode == "inpaint" and uploaded.get("mask"):
                mi = g.add("LoadImage", image=uploaded["mask"])
                ms = g.add("ImageScale", image=G.r(mi), upscale_method="nearest-exact", width=w, height=h,
                           crop="center")
                mask = g.add("ImageToMask", image=G.r(ms), channel="red")
                grow = g.add("GrowMask", mask=G.r(mask), expand=8, tapered_corners=True)
                latent = G.r(g.add("SetLatentNoiseMask", samples=latent, mask=G.r(grow)))
                denoise = float(p.get("denoise", 0.9))
        else:
            latent = G.r(g.add("EmptySD3LatentImage", width=w, height=h, batch_size=1))
        sampler_name = p.get("sampler") or "res_multistep"
        scheduler = p.get("scheduler") or "simple"
        ks = g.add("KSampler", model=model, positive=pos, negative=neg, latent_image=latent, seed=seed, steps=steps,
                   cfg=cfg, sampler_name=sampler_name, scheduler=scheduler, denoise=denoise)
        img = decode(G.r(ks))
        detail_model, detail_sampler = model, sampler_name

    if p.get("upscale"):
        img = _upscale_detail(g, img, detail_model, pos, neg, vae, seed, factor=float(p.get("upscale_factor", 2.0)),
                              detail=float(p.get("detail", 0.3)) if detail_model is not None else 0,
                              sampler=detail_sampler or "res_multistep")
    g.add("SaveImage", images=img, filename_prefix="localai/img")
    return g.nodes, {"seed": seed, "width": w, "height": h, "steps": steps, "cfg": cfg}


# ───────────────────────────── video (LTX-2.5) ─────────────────────────────
# Mirrors the official ComfyUI "LTX-2.5 T2V / I2V" templates (native nodes only), with the
# Blackwell-native nvfp4 distilled transformer and the lighter convolutional video VAE.
LTX = {
    "unet": "ltx-2.5-22b-distilled-transformer-nvfp4.safetensors",
    "video_vae": "ltx-2.5-video-vae-conv-bf16.safetensors",
    "audio_vae": "ltx-2.5-audio-vae-bf16.safetensors",
    "text_encoder": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
    "upscaler": "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
    "duration_head": "ltx-2.5-duration-head-bf16.safetensors",
}
STAGE1_SIGMAS = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
STAGE2_SIGMAS = "0.85, 0.7250, 0.4219, 0.0"
DEFAULT_VIDEO_NEGATIVE = "pc game, console game, video game, cartoon, childish, ugly"


def build_avatar(p: dict, uploaded: dict) -> tuple[dict, dict]:
    """Talking avatar: portrait image + fixed speech audio -> lip-synced video (LTX-2.5, official IA2V recipe).

    The speech is VAE-encoded into the audio latent with a zero noise mask, so the model keeps the audio
    unchanged and generates matching mouth movement, blinking and natural head motion.
    """
    g = G()
    seed = seed_or_random(p.get("seed"))
    fps = int(p.get("fps", 24))
    duration = float(p["duration"])
    frames = video_frames(duration, fps)
    two_stage = p.get("two_stage", True)
    align = 64 if two_stage else 32
    w, h = int(p["width"]) // align * align, int(p["height"]) // align * align
    model = G.r(g.add("UNETLoader", unet_name=LTX["unet"], weight_dtype="default"))
    vae = G.r(g.add("VAELoader", vae_name=LTX["video_vae"]))
    audio_vae = G.r(g.add("VAELoader", vae_name=LTX["audio_vae"]))
    clip = G.r(g.add("CLIPLoader", clip_name=LTX["text_encoder"], type="ltxv", device="default"))
    pos = G.r(g.add("CLIPTextEncode", clip=clip, text=p.get("prompt", "")))
    neg = G.r(g.add("CLIPTextEncode", clip=clip, text=p.get("negative") or
                    DEFAULT_VIDEO_NEGATIVE + ", distorted face, deformed mouth, extra teeth, morphing, flicker"))
    cond = g.add("LTXVConditioning", positive=pos, negative=neg, frame_rate=float(fps))
    pos, neg = G.r(cond, 0), G.r(cond, 1)

    def guider():
        return G.r(g.add("LTXVDualCFGGuider", model=model, positive=pos, negative=neg,
                         video_cfg=float(p.get("cfg", 1.0)), audio_cfg=1.0))

    img = G.r(g.add("LoadImage", image=uploaded["image"]))
    img = G.r(g.add("ImageScale", image=img, upscale_method="lanczos", width=w, height=h, crop="center"))
    prep = G.r(g.add("LTXVPreprocess", image=img, img_compression=int(p.get("img_compression", 18))))
    sw, sh = (w // 2, h // 2) if two_stage else (w, h)
    latent = G.r(g.add("EmptyLTXVLatentVideo", width=sw, height=sh, length=frames, batch_size=1))
    latent = G.r(g.add("LTXVImgToVideoInplace", vae=vae, image=prep, latent=latent,
                       strength=float(p.get("image_strength", 0.7 if two_stage else 1.0)), bypass=False))
    audio = G.r(g.add("LoadAudio", audio=uploaded["audio"]))
    audio = G.r(g.add("TrimAudioDuration", audio=audio, start_index=0.0, duration=duration))
    alat = G.r(g.add("LTXVAudioVAEEncode", audio=audio, audio_vae=audio_vae))
    keep = G.r(g.add("SolidMask", value=0.0, width=1024, height=1024))       # 0 = never re-noise the speech
    alat = G.r(g.add("SetLatentNoiseMask", samples=alat, mask=keep))
    av = G.r(g.add("LTXVConcatAVLatent", video_latent=latent, audio_latent=alat))
    s1 = g.add("SamplerCustomAdvanced", noise=G.r(g.add("RandomNoise", noise_seed=seed)), guider=guider(),
               sampler=G.r(g.add("KSamplerSelect", sampler_name="euler_ancestral")),
               sigmas=G.r(g.add("ManualSigmas", sigmas=STAGE1_SIGMAS)), latent_image=av)
    sep = g.add("LTXVSeparateAVLatent", av_latent=G.r(s1))
    vid_lat, aud_lat = G.r(sep, 0), G.r(sep, 1)
    if two_stage:
        up = G.r(g.add("LTXVLatentUpsampler", samples=vid_lat,
                       upscale_model=G.r(g.add("LatentUpscaleModelLoader", model_name=LTX["upscaler"])), vae=vae))
        up = G.r(g.add("LTXVImgToVideoInplace", vae=vae, image=prep, latent=up, strength=1.0, bypass=False))
        av2 = G.r(g.add("LTXVConcatAVLatent", video_latent=up, audio_latent=aud_lat))
        s2 = g.add("SamplerCustomAdvanced", noise=G.r(g.add("RandomNoise", noise_seed=seed + 1)), guider=guider(),
                   sampler=G.r(g.add("KSamplerSelect", sampler_name="euler_ancestral")),
                   sigmas=G.r(g.add("ManualSigmas", sigmas=STAGE2_SIGMAS)), latent_image=av2)
        vid_lat = G.r(g.add("LTXVSeparateAVLatent", av_latent=G.r(s2)), 0)
    images = G.r(g.add("VAEDecodeTiled", samples=vid_lat, vae=vae, tile_size=512, overlap=64, temporal_size=64,
                       temporal_overlap=16))
    if p.get("audio_from_model"):   # diagnostics: the audio the model actually conditioned on
        audio = G.r(g.add("LTXVAudioVAEDecode", samples=aud_lat, audio_vae=audio_vae))
    v = g.add("CreateVideo", images=images, fps=float(fps), audio=audio)   # original speech, not re-decoded
    g.add("SaveVideo", video=G.r(v), filename_prefix="localai/avatar", format="auto", codec="auto")
    return g.nodes, {"seed": seed, "width": w, "height": h, "frames": frames, "fps": fps,
                     "duration": round((frames - 1) / fps, 2)}


def video_frames(duration: float, fps: int) -> int:
    """LTX needs 8k+1 frames."""
    n = max(9, round(duration * fps))
    return (n // 8) * 8 + 1


def build_video(p: dict, uploaded: dict) -> tuple[dict, dict]:
    g = G()
    seed = seed_or_random(p.get("seed"))
    two_stage = p.get("two_stage", True)
    align = 64 if two_stage else 32
    w, h = int(p["width"]) // align * align, int(p["height"]) // align * align
    fps = int(p.get("fps", 24))
    frames = int(p["frames"]) if p.get("frames") else video_frames(float(p.get("duration", 5)), fps)
    frames = ((frames - 1) // 8) * 8 + 1
    video_cfg = float(p.get("cfg", 1.0))
    audio_cfg = float(p.get("audio_cfg", 1.0))
    image_name = uploaded.get("image") if p.get("mode") == "i2v" else None

    model = G.r(g.add("UNETLoader", unet_name=LTX["unet"], weight_dtype="default"))
    vae = G.r(g.add("VAELoader", vae_name=LTX["video_vae"]))
    audio_vae = G.r(g.add("VAELoader", vae_name=LTX["audio_vae"]))
    clip = G.r(g.add("CLIPLoader", clip_name=LTX["text_encoder"], type="ltxv", device="default"))
    pos = G.r(g.add("CLIPTextEncode", clip=clip, text=p.get("prompt", "")))
    neg = G.r(g.add("CLIPTextEncode", clip=clip, text=p.get("negative") or DEFAULT_VIDEO_NEGATIVE))
    length = frames
    if p.get("auto_duration"):
        # LTX-2.5 duration head predicts a natural shot length for the prompt (snapped to 8k+1 frames)
        head = g.add("ModelPatchLoader", name=LTX["duration_head"])
        pred = g.add("LTXVDurationPredictor", model=model, positive=pos, duration_head=G.r(head),
                     frame_rate=float(fps), min_seconds=2.0, max_seconds=float(p.get("max_seconds", 10)))
        length = G.r(pred, 0)
    cond = g.add("LTXVConditioning", positive=pos, negative=neg, frame_rate=float(fps))
    pos, neg = G.r(cond, 0), G.r(cond, 1)

    def guider():
        return G.r(g.add("LTXVDualCFGGuider", model=model, positive=pos, negative=neg,
                         video_cfg=video_cfg, audio_cfg=audio_cfg))

    sw, sh = (w // 2, h // 2) if two_stage else (w, h)
    latent = G.r(g.add("EmptyLTXVLatentVideo", width=sw, height=sh, length=length, batch_size=1))
    prep = None
    if image_name:
        li = g.add("LoadImage", image=image_name)
        rs = g.add("ImageScale", image=G.r(li), upscale_method="lanczos", width=w, height=h, crop="center")
        prep = G.r(g.add("LTXVPreprocess", image=G.r(rs), img_compression=int(p.get("img_compression", 18))))
        latent = G.r(g.add("LTXVImgToVideoInplace", vae=vae, image=prep, latent=latent,
                           strength=float(p.get("image_strength", 0.7 if two_stage else 1.0)), bypass=False))
    alat = G.r(g.add("LTXVEmptyLatentAudio", audio_vae=audio_vae, frames_number=length, frame_rate=fps, batch_size=1))
    av = G.r(g.add("LTXVConcatAVLatent", video_latent=latent, audio_latent=alat))
    s1 = g.add("SamplerCustomAdvanced", noise=G.r(g.add("RandomNoise", noise_seed=seed)), guider=guider(),
               sampler=G.r(g.add("KSamplerSelect", sampler_name="euler_ancestral")),
               sigmas=G.r(g.add("ManualSigmas", sigmas=STAGE1_SIGMAS)), latent_image=av)
    sep1 = g.add("LTXVSeparateAVLatent", av_latent=G.r(s1))
    vid_lat, aud_lat = G.r(sep1, 0), G.r(sep1, 1)

    if two_stage:
        upm = g.add("LatentUpscaleModelLoader", model_name=LTX["upscaler"])
        up = G.r(g.add("LTXVLatentUpsampler", samples=vid_lat, upscale_model=G.r(upm), vae=vae))
        if prep is not None:
            up = G.r(g.add("LTXVImgToVideoInplace", vae=vae, image=prep, latent=up, strength=1.0, bypass=False))
        av2 = G.r(g.add("LTXVConcatAVLatent", video_latent=up, audio_latent=aud_lat))
        s2 = g.add("SamplerCustomAdvanced", noise=G.r(g.add("RandomNoise", noise_seed=seed + 1)), guider=guider(),
                   sampler=G.r(g.add("KSamplerSelect", sampler_name="euler_ancestral")),
                   sigmas=G.r(g.add("ManualSigmas", sigmas=STAGE2_SIGMAS)), latent_image=av2)
        sep2 = g.add("LTXVSeparateAVLatent", av_latent=G.r(s2))
        vid_lat, aud_lat = G.r(sep2, 0), G.r(sep2, 1)

    images = G.r(g.add("VAEDecodeTiled", samples=vid_lat, vae=vae, tile_size=int(p.get("vae_tile", 512)), overlap=64,
                       temporal_size=int(p.get("vae_temporal", 64)), temporal_overlap=16))
    cv = {"images": images, "fps": float(fps)}
    if p.get("audio", True):
        cv["audio"] = G.r(g.add("LTXVAudioVAEDecode", samples=aud_lat, audio_vae=audio_vae))
    video = g.add("CreateVideo", **cv)
    # "auto" = MP4 container with H.264 (browser-playable), as in the official template
    g.add("SaveVideo", video=G.r(video), filename_prefix="localai/vid", format="auto", codec="auto")
    auto = bool(p.get("auto_duration"))
    return g.nodes, {"seed": seed, "width": w, "height": h, "frames": "auto" if auto else frames, "fps": fps,
                     "duration": "auto" if auto else round((frames - 1) / fps, 2)}
