"""ComfyUI graphs for the tool categories (native nodes only, mirroring the official ComfyUI templates).

Image:  Qwen-Image-2.1 (unified generate + instruction edit), SeedVR2 upscale, BiRefNet background removal
Video:  Bernini-R (instruction video editing), SeedVR2 video upscale, RIFE frame interpolation
"""
from workflows import G, seed_or_random

QWEN = {"unet": "qwen_image_2.1_int8_convrot.safetensors",
        "clip": "qwen3vl_8b_int8_convrot.safetensors",
        "vae": "qwen_image_2.1_vae_bf16.safetensors"}
SEEDVR2 = {"unet": "seedvr2_7b_int8_convrot.safetensors", "vae": "seedvr2_ema_vae_fp16.safetensors"}
RIFE = "rife_v4.25_heavy.safetensors"
BIREFNET = "birefnet.safetensors"
UPSCALER_FAST = "RealESRGAN_x4plus.pth"
BERNINI = {"high": "wan2.2_bernini_r_high_noise_fp8_scaled.safetensors",
           "low": "wan2.2_bernini_r_low_noise_fp8_scaled.safetensors",
           "lora": "lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors",
           "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
           "vae": "wan_2.1_vae.safetensors"}
VACE = {"unet": "wan2.1_vace_1.3B_fp16.safetensors",
        "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "vae": "wan_2.1_vae.safetensors"}
BERNINI_TASKS = {
    "edit": "You are a helpful assistant specialized in video editing.",
    "propagate": "You are a helpful assistant specialized in video editing on content propagation.",
    "reference": "You are a helpful assistant specialized in video editing with reference.",
    "insert": "You are a helpful assistant specialized in ads insertion.",
    "action": "You are a helpful assistant for editing. You may need to adjust the subject's action or position.",
    "style": ("You are a helpful assistant for editing. You might need to adjust the video's style, lighting, colors, "
              "textures, and the subject's pose or action."),
}
WAN_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，"
                "多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")


# ─────────────────────────── Qwen-Image-2.1 ───────────────────────────
def build_qwen_image(p: dict, uploaded: dict) -> tuple[dict, dict]:
    """Text-to-image, or instruction edit when uploaded['image'] (+ optional refs / mask) is given."""
    g = G()
    seed = seed_or_random(p.get("seed"))
    steps, cfg = int(p.get("steps", 25)), float(p.get("cfg", 1.0))
    model = G.r(g.add("QwenImage21Cache", model=G.r(g.add("UNETLoader", unet_name=QWEN["unet"], weight_dtype="default")),
                      device="auto", dtype="default"))
    clip = G.r(g.add("CLIPLoader", clip_name=QWEN["clip"], type="qwen_image", device="default"))
    vae = G.r(g.add("VAELoader", vae_name=QWEN["vae"]))
    images = {}
    src = None
    names = ([uploaded["image"]] if uploaded.get("image") else []) + list(uploaded.get("refs") or [])
    for i, name in enumerate(names[:10]):
        li = G.r(g.add("LoadImage", image=name))
        if i == 0:
            src = li
        images[f"images.image_{i + 1}"] = li
    enc = g.add("TextEncodeQwenImage21", clip=clip, prompt=p.get("prompt", ""), negative_prompt=p.get("negative") or "",
                resolution=int(p.get("resolution", 0 if src else 1024)), vae=vae, **images)
    if src is not None:
        latent = G.r(enc, 2)                        # edit latent sized from the input image
    else:
        w, h = int(p.get("width", 1024)) // 16 * 16, int(p.get("height", 1024)) // 16 * 16
        latent = G.r(g.add("EmptyLatentImage", width=w, height=h, batch_size=1))
    ks = g.add("KSampler", model=model, positive=G.r(enc, 0), negative=G.r(enc, 1), latent_image=latent, seed=seed,
               steps=steps, cfg=cfg, sampler_name=p.get("sampler") or "euler", scheduler=p.get("scheduler") or "simple",
               denoise=1.0)
    img = G.r(g.add("VAEDecode", samples=G.r(ks), vae=vae))
    if src is not None and uploaded.get("mask"):
        # Mask given: only the white area may change; everything else is copied back from the original.
        mi = g.add("LoadImage", image=uploaded["mask"])
        size = g.add("GetImageSize", image=img)
        orig = G.r(g.add("ImageScale", image=src, upscale_method="lanczos", width=G.r(size, 0), height=G.r(size, 1),
                         crop="disabled"))
        ms = g.add("ImageScale", image=G.r(mi), upscale_method="bilinear", width=G.r(size, 0), height=G.r(size, 1),
                   crop="disabled")
        mask = g.add("ImageToMask", image=G.r(ms), channel="red")
        mask = g.add("GrowMask", mask=G.r(mask), expand=int(p.get("mask_grow", 6)), tapered_corners=True)
        mask = g.add("FeatherMask", mask=G.r(mask), left=12, top=12, right=12, bottom=12)
        img = G.r(g.add("ImageCompositeMasked", destination=orig, source=img, x=0, y=0, resize_source=False,
                        mask=G.r(mask)))
    g.add("SaveImage", images=img, filename_prefix="localai/qwen")
    return g.nodes, {"seed": seed, "steps": steps, "cfg": cfg}


# ─────────────────────────── upscaling (SeedVR2 / Real-ESRGAN) ───────────────────────────
def _seedvr2(g, images, factor=None, shorter=None, video=False, color="lab", seed=0):
    model = G.r(g.add("UNETLoader", unet_name=SEEDVR2["unet"], weight_dtype="default"))
    vae = G.r(g.add("VAELoader", vae_name=SEEDVR2["vae"]))
    if shorter:
        resized = G.r(g.add("ResizeImageMaskNode", input=images, scale_method="lanczos",
                            **{"resize_type": "scale shorter dimension", "resize_type.shorter_size": int(shorter)}))
    else:
        resized = G.r(g.add("ResizeImageMaskNode", input=images, scale_method="lanczos",
                            **{"resize_type": "scale by multiplier", "resize_type.multiplier": float(factor)}))
    pre = G.r(g.add("SeedVR2Preprocess", resized_images=resized))
    lat = G.r(g.add("VAEEncodeTiled", pixels=pre, vae=vae, tile_size=512, overlap=128,
                    temporal_size=64 if video else 4096, temporal_overlap=8))
    if video:
        chunk = g.add("SeedVR2TemporalChunk", latent=lat, temporal_overlap=2, chunking_mode="auto")
        lat_in = G.r(chunk, 0)
    else:
        lat_in = lat
    cond = g.add("SeedVR2Conditioning", model=model, vae_conditioning=lat_in)
    ks = G.r(g.add("KSampler", model=model, positive=G.r(cond, 0), negative=G.r(cond, 1), latent_image=lat_in,
                   seed=seed, steps=1, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0))
    if video:
        ks = G.r(g.add("SeedVR2TemporalMerge", latents=ks, temporal_overlap=G.r(chunk, 1)))
    dec = G.r(g.add("VAEDecodeTiled", samples=ks, vae=vae, tile_size=512, overlap=128,
                    temporal_size=64 if video else 4096, temporal_overlap=8))
    return G.r(g.add("SeedVR2PostProcessing", images=dec, original_resized_images=resized,
                     color_correction_method=color))


def _esrgan(g, images, factor=None, shorter=None):
    up = G.r(g.add("ImageUpscaleWithModel", upscale_model=G.r(g.add("UpscaleModelLoader", model_name=UPSCALER_FAST)),
                   image=images))
    if shorter:
        return G.r(g.add("ResizeImageMaskNode", input=up, scale_method="lanczos",
                         **{"resize_type": "scale shorter dimension", "resize_type.shorter_size": int(shorter)}))
    return G.r(g.add("ImageScaleBy", image=up, upscale_method="lanczos", scale_by=float(factor) / 4.0))


def build_upscale_image(p: dict, uploaded: dict) -> tuple[dict, dict]:
    g = G()
    img = G.r(g.add("LoadImage", image=uploaded["image"]))
    factor = float(p.get("factor", 2))
    if p.get("quality", "balanced") == "fast":
        out = _esrgan(g, img, factor=factor)
    else:
        out = _seedvr2(g, img, factor=factor, color=p.get("color", "lab"), seed=seed_or_random(p.get("seed")))
    g.add("SaveImage", images=out, filename_prefix="localai/upscale")
    return g.nodes, {"factor": factor}


def build_upscale_video_segment(p: dict, video_name: str) -> dict:
    """One segment (no audio handling here; the orchestrator re-muxes the original audio)."""
    g = G()
    comp = g.add("GetVideoComponents", video=G.r(g.add("LoadVideo", file=video_name)))
    frames = G.r(comp, 0)
    target = p.get("target_height")
    factor = None if target else float(p.get("factor", 2))
    if p.get("quality", "balanced") == "fast":
        out = _esrgan(g, frames, factor=factor, shorter=target)
    else:
        out = _seedvr2(g, frames, factor=factor, shorter=target, video=True, seed=int(p.get("seed", 1)))
    v = g.add("CreateVideo", images=out, fps=G.r(comp, 2))
    g.add("SaveVideo", video=G.r(v), filename_prefix="localai/seg", format="auto", codec="auto")
    return g.nodes


# ─────────────────────────── frame interpolation (RIFE) ───────────────────────────
def build_interpolate_segment(p: dict, video_name: str, fps_out: float) -> dict:
    g = G()
    comp = g.add("GetVideoComponents", video=G.r(g.add("LoadVideo", file=video_name)))
    model = g.add("FrameInterpolationModelLoader", model_name=RIFE)
    frames = G.r(g.add("FrameInterpolate", interp_model=G.r(model), images=G.r(comp, 0),
                       multiplier=int(p.get("multiplier", 2))))
    v = g.add("CreateVideo", images=frames, fps=float(fps_out))
    g.add("SaveVideo", video=G.r(v), filename_prefix="localai/seg", format="auto", codec="auto")
    return g.nodes


# ─────────────────────────── background removal (BiRefNet) ───────────────────────────
def build_remove_bg(p: dict, uploaded: dict) -> tuple[dict, dict]:
    g = G()
    img = G.r(g.add("LoadImage", image=uploaded["image"]))
    mask = G.r(g.add("RemoveBackground", image=img,
                     bg_removal_model=G.r(g.add("LoadBackgroundRemovalModel", bg_removal_name=BIREFNET))))
    inv = G.r(g.add("InvertMask", mask=mask))
    out = G.r(g.add("JoinImageWithAlpha", image=img, alpha=inv))
    g.add("SaveImage", images=out, filename_prefix="localai/nobg")
    return g.nodes, {}


# ─────────────────────────── HunyuanVideo 1.5 (alternative video generator) ───────────────────────────
HUNYUAN = {"t2v": "hunyuanvideo1.5_480p_t2v_cfg_distilled_fp8_scaled.safetensors",
           "i2v": "hunyuanvideo1.5_480p_i2v_step_distilled_fp8_scaled.safetensors",
           "sr": "hunyuanvideo1.5_720p_sr_distilled_fp8_scaled.safetensors",
           "clip1": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "clip2": "byt5_small_glyphxl_fp16.safetensors",
           "vae": "hunyuanvideo15_vae_fp16.safetensors", "vision": "sigclip_vision_patch14_384.safetensors",
           "upsampler": "hunyuanvideo15_latent_upsampler_720p.safetensors",
           "lora4": "hunyuanvideo1.5_t2v_480p_lightx2v_4step_lora_rank_32_bf16.safetensors"}


def build_hunyuan(p: dict, uploaded: dict) -> tuple[dict, dict]:
    """480p generation (T2V cfg-distilled or I2V step-distilled) + optional official 720p super-resolution."""
    g = G()
    seed = seed_or_random(p.get("seed"))
    landscape = int(p.get("width", 848)) >= int(p.get("height", 480))
    w, h = (848, 480) if landscape else (480, 848)
    fps = int(p.get("fps", 24))
    frames = ((int(round(float(p.get("duration", 5)) * fps)) - 1) // 4) * 4 + 1   # 4k+1 frames
    i2v = bool(uploaded.get("image"))
    fast = p.get("hy_preset") == "fast"
    clip = G.r(g.add("DualCLIPLoader", clip_name1=HUNYUAN["clip1"], clip_name2=HUNYUAN["clip2"],
                     type="hunyuan_video_15", device="default"))
    vae = G.r(g.add("VAELoader", vae_name=HUNYUAN["vae"]))
    pos = G.r(g.add("CLIPTextEncode", clip=clip, text=p.get("prompt", "")))
    neg = G.r(g.add("CLIPTextEncode", clip=clip, text=p.get("negative") or ""))
    model = G.r(g.add("UNETLoader", unet_name=HUNYUAN["i2v" if i2v else "t2v"], weight_dtype="default"))
    img = vis = None
    if i2v:
        img = G.r(g.add("LoadImage", image=uploaded["image"]))
        img = G.r(g.add("ImageScale", image=img, upscale_method="lanczos", width=w, height=h, crop="center"))
        vis = G.r(g.add("CLIPVisionEncode", clip_vision=G.r(g.add("CLIPVisionLoader", clip_name=HUNYUAN["vision"])),
                        image=img, crop="center"))
        cond = g.add("HunyuanVideo15ImageToVideo", positive=pos, negative=neg, vae=vae, width=w, height=h,
                     length=frames, batch_size=1, start_image=img, clip_vision_output=vis)
        pos, neg, latent = G.r(cond, 0), G.r(cond, 1), G.r(cond, 2)
        steps, cfg = (8, 1.0) if fast else (12, 1.0)          # step-distilled model
    else:
        latent = G.r(g.add("EmptyHunyuanVideo15Latent", width=w, height=h, length=frames, batch_size=1))
        if fast:
            model = G.r(g.add("LoraLoaderModelOnly", model=model, lora_name=HUNYUAN["lora4"], strength_model=1.0))
            steps, cfg = 4, 1.0
        else:
            steps, cfg = 30, 1.0                                # cfg-distilled: no CFG pass needed
    ms = G.r(g.add("ModelSamplingSD3", model=model, shift=7.0))
    guider = g.add("CFGGuider", model=ms, positive=pos, negative=neg, cfg=cfg)
    s1 = g.add("SamplerCustomAdvanced", noise=G.r(g.add("RandomNoise", noise_seed=seed)), guider=G.r(guider),
               sampler=G.r(g.add("KSamplerSelect", sampler_name="euler")),
               sigmas=G.r(g.add("BasicScheduler", model=ms, scheduler="simple", steps=steps, denoise=1.0)),
               latent_image=latent)
    out_lat = G.r(s1, 0)
    ow, oh = w, h
    if p.get("two_stage", True):                               # official 720p super-resolution stage
        ow, oh = (1280, 720) if landscape else (720, 1280)
        up = G.r(g.add("HunyuanVideo15LatentUpscaleWithModel",
                       model=G.r(g.add("LatentUpscaleModelLoader", model_name=HUNYUAN["upsampler"])),
                       samples=out_lat, upscale_method="bilinear", width=ow, height=oh, crop="disabled"))
        sr_extra = {"vae": vae}
        if i2v:
            img_hi = G.r(g.add("ImageScale", image=img, upscale_method="lanczos", width=ow, height=oh, crop="center"))
            sr_extra.update(start_image=img_hi, clip_vision_output=vis)
        src = g.add("HunyuanVideo15SuperResolution", positive=pos, negative=neg, latent=up, noise_augmentation=0.7,
                    **sr_extra)
        srm = G.r(g.add("ModelSamplingSD3", model=G.r(g.add("UNETLoader", unet_name=HUNYUAN["sr"],
                                                             weight_dtype="default")), shift=2.0))
        s2 = g.add("SamplerCustomAdvanced", noise=G.r(g.add("RandomNoise", noise_seed=seed + 1)),
                   guider=G.r(g.add("CFGGuider", model=srm, positive=G.r(src, 0), negative=G.r(src, 1), cfg=1.0)),
                   sampler=G.r(g.add("KSamplerSelect", sampler_name="euler")),
                   sigmas=G.r(g.add("BasicScheduler", model=srm, scheduler="simple", steps=6, denoise=1.0)),
                   latent_image=G.r(src, 2))
        out_lat = G.r(s2, 0)
    images = G.r(g.add("VAEDecodeTiled", samples=out_lat, vae=vae, tile_size=512, overlap=64, temporal_size=32,
                       temporal_overlap=8))
    v = g.add("CreateVideo", images=images, fps=float(fps))
    g.add("SaveVideo", video=G.r(v), filename_prefix="localai/hunyuan", format="auto", codec="auto")
    return g.nodes, {"seed": seed, "width": ow, "height": oh, "frames": frames, "fps": fps, "steps": steps,
                     "duration": round((frames - 1) / fps, 2), "mode": "i2v" if i2v else "t2v"}


# ─────────────────────────── InfiniteTalk (audio-driven lip sync / talking avatar) ───────────────────────────
INFTALK = {"unet": "Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ.safetensors",
           "patch": "wan2.1_infiniteTalk_multi_fp16.safetensors",
           "lora": "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
           "audio_enc": "wav2vec2-chinese-base_fp16.safetensors",
           "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "vae": "wan_2.1_vae.safetensors"}
INFTALK_FPS = 25
MOTION_FRAMES = 9


def build_infinitetalk_chunk(p: dict, image_name: str, audio_name: str, prev_video: str | None, width: int,
                             height: int, length: int, audio2_name: str | None = None, masks: tuple | None = None) -> dict:
    """One InfiniteTalk chunk (length = 4k+1 frames at 25 fps).

    audio_name must start at this chunk's first frame; prev_video holds the last MOTION_FRAMES frames of the
    previous chunk (continuation) - their audio is at the very start of audio_name.
    """
    g = G()
    model = G.r(g.add("UNETLoader", unet_name=INFTALK["unet"], weight_dtype="default"))
    model = G.r(g.add("LoraLoaderModelOnly", model=model, lora_name=INFTALK["lora"], strength_model=1.0))
    patch = G.r(g.add("ModelPatchLoader", name=INFTALK["patch"]))
    clip = G.r(g.add("CLIPLoader", clip_name=INFTALK["clip"], type="wan", device="default"))
    vae = G.r(g.add("VAELoader", vae_name=INFTALK["vae"]))
    enc = G.r(g.add("AudioEncoderLoader", audio_encoder_name=INFTALK["audio_enc"]))
    pos = G.r(g.add("CLIPTextEncode", clip=clip, text=p.get("prompt") or
                    "A person is talking naturally to the camera, subtle head movement, natural blinking."))
    neg = G.r(g.add("ConditioningZeroOut", conditioning=pos))
    a1 = G.r(g.add("AudioEncoderEncode", audio_encoder=enc, audio=G.r(g.add("LoadAudio", audio=audio_name))))
    img = G.r(g.add("LoadImage", image=image_name))
    kw = {"start_image": img, "mode": "single_speaker"}
    if audio2_name and masks:
        a2 = G.r(g.add("AudioEncoderEncode", audio_encoder=enc, audio=G.r(g.add("LoadAudio", audio=audio2_name))))
        m1 = G.r(g.add("ImageToMask", image=G.r(g.add("LoadImage", image=masks[0])), channel="red"))
        m2 = G.r(g.add("ImageToMask", image=G.r(g.add("LoadImage", image=masks[1])), channel="red"))
        kw.update({"mode": "two_speakers", "mode.audio_encoder_output_2": a2, "mode.mask_1": m1, "mode.mask_2": m2})
    if prev_video:
        kw["previous_frames"] = G.r(g.add("GetVideoComponents", video=G.r(g.add("LoadVideo", file=prev_video))), 0)
    it = g.add("WanInfiniteTalkToVideo", model=model, model_patch=patch, positive=pos, negative=neg, vae=vae,
               width=width, height=height, length=length, audio_encoder_output_1=a1,
               motion_frame_count=MOTION_FRAMES, audio_scale=float(p.get("audio_scale", 1.0)), **kw)
    s = g.add("SamplerCustomAdvanced", noise=G.r(g.add("RandomNoise", noise_seed=int(p.get("seed", 1)))),
              guider=G.r(g.add("CFGGuider", model=G.r(it, 0), positive=G.r(it, 1), negative=G.r(it, 2), cfg=1.0)),
              sampler=G.r(g.add("KSamplerSelect", sampler_name="euler")),
              sigmas=G.r(g.add("BasicScheduler", model=G.r(it, 0), scheduler="normal",
                               steps=int(p.get("steps", 6)), denoise=1.0)),
              latent_image=G.r(it, 3))
    images = G.r(g.add("VAEDecode", samples=G.r(s, 0), vae=vae))
    v = g.add("CreateVideo", images=images, fps=float(INFTALK_FPS))
    g.add("SaveVideo", video=G.r(v), filename_prefix="localai/talk", format="auto", codec="auto")
    return g.nodes


# ─────────────────────────── video editing (Bernini-R) ───────────────────────────
def build_video_edit(p: dict, video_name: str, uploaded: dict, width: int, height: int, length: int,
                     fps: float) -> tuple[dict, dict]:
    """Build a video-to-video edit with VACE 1.3B or Bernini-R 14B."""
    g = G()
    seed = seed_or_random(p.get("seed"))
    if p.get("model") == "vace":
        comp = g.add("GetVideoComponents", video=G.r(g.add("LoadVideo", file=video_name)))
        clip = G.r(g.add("CLIPLoader", clip_name=VACE["clip"], type="wan", device="default"))
        vae = G.r(g.add("VAELoader", vae_name=VACE["vae"]))
        model = G.r(g.add("UNETLoader", unet_name=VACE["unet"], weight_dtype="default"))
        model = G.r(g.add("ModelSamplingSD3", model=model, shift=8.0))
        task = "reference" if uploaded.get("image") else p.get("task", "edit")
        text = BERNINI_TASKS[task] + (p.get("prompt") or "")
        pos = G.r(g.add("CLIPTextEncode", clip=clip, text=text))
        neg = G.r(g.add("CLIPTextEncode", clip=clip, text=WAN_NEGATIVE))
        extra = {"reference_image": G.r(g.add("LoadImage", image=uploaded["image"]))} if uploaded.get("image") else {}
        cond = g.add("WanVaceToVideo", positive=pos, negative=neg, vae=vae, width=width, height=height,
                     length=length, batch_size=1, strength=1.0, control_video=G.r(comp, 0), **extra)
        ks = g.add("KSampler", model=model, positive=G.r(cond, 0), negative=G.r(cond, 1), latent_image=G.r(cond, 2),
                   seed=seed, steps=int(p.get("steps", 20)), cfg=float(p.get("cfg", 6.0)),
                   sampler_name="uni_pc", scheduler="simple", denoise=1.0)
        trimmed = G.r(g.add("TrimVideoLatent", samples=G.r(ks), trim_amount=G.r(cond, 3)))
        images = G.r(g.add("VAEDecode", samples=trimmed, vae=vae))
        v = g.add("CreateVideo", images=images, fps=float(fps))
        g.add("SaveVideo", video=G.r(v), filename_prefix="localai/vedit_vace", format="auto", codec="auto")
        return g.nodes, {"seed": seed, "steps": int(p.get("steps", 20)), "task": task,
                         "width": width, "height": height, "frames": length, "model": "Wan 2.1 VACE 1.3B"}

    comp = g.add("GetVideoComponents", video=G.r(g.add("LoadVideo", file=video_name)))
    clip = G.r(g.add("CLIPLoader", clip_name=BERNINI["clip"], type="wan", device="default"))
    vae = G.r(g.add("VAELoader", vae_name=BERNINI["vae"]))
    high = G.r(g.add("UNETLoader", unet_name=BERNINI["high"], weight_dtype="default"))
    low = G.r(g.add("UNETLoader", unet_name=BERNINI["low"], weight_dtype="default"))
    turbo = p.get("turbo", True)
    if turbo:
        high = G.r(g.add("LoraLoaderModelOnly", model=high, lora_name=BERNINI["lora"], strength_model=3.0))
        low = G.r(g.add("LoraLoaderModelOnly", model=low, lora_name=BERNINI["lora"], strength_model=1.5))
    steps, split = (6, 3) if turbo else (40, 20)
    cfg = 1.0 if turbo else 5.0
    task = "reference" if uploaded.get("image") else p.get("task", "edit")
    text = BERNINI_TASKS[task] + (p.get("prompt") or "")
    pos = G.r(g.add("CLIPTextEncode", clip=clip, text=text))
    neg = G.r(g.add("CLIPTextEncode", clip=clip, text=WAN_NEGATIVE))
    extra = {}
    if uploaded.get("image"):
        extra["reference_images.reference_image_0"] = G.r(g.add("LoadImage", image=uploaded["image"]))
    cond = g.add("BerniniConditioning", positive=pos, negative=neg, vae=vae, width=width, height=height, length=length,
                 batch_size=1, source_video=G.r(comp, 0), ref_max_size=848, **extra)
    sig = g.add("SplitSigmas", sigmas=G.r(g.add("BasicScheduler", model=low, scheduler="simple", steps=steps, denoise=1.0)),
                step=split)
    sampler = G.r(g.add("KSamplerSelect", sampler_name="res_multistep"))
    s1 = g.add("SamplerCustom", model=high, add_noise=True, noise_seed=seed, cfg=cfg, positive=G.r(cond, 0),
               negative=G.r(cond, 1), sampler=sampler, sigmas=G.r(sig, 0), latent_image=G.r(cond, 2))
    s2 = g.add("SamplerCustom", model=low, add_noise=False, noise_seed=0, cfg=cfg, positive=G.r(cond, 0),
               negative=G.r(cond, 1), sampler=sampler, sigmas=G.r(sig, 1), latent_image=G.r(s1, 0))
    images = G.r(g.add("VAEDecode", samples=G.r(s2, 0), vae=vae))
    v = g.add("CreateVideo", images=images, fps=float(fps))       # audio is re-muxed by the orchestrator
    g.add("SaveVideo", video=G.r(v), filename_prefix="localai/vedit", format="auto", codec="auto")
    return g.nodes, {"seed": seed, "steps": steps, "task": task, "width": width, "height": height, "frames": length}
