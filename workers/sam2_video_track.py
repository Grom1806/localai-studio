"""Local SAM 2.1 mask tracking worker for LocalAI Studio."""
import argparse
import gc
import math
import subprocess
import sys
from pathlib import Path


def install(model_dir: Path):
    from huggingface_hub import snapshot_download

    model_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id="facebook/sam2.1-hiera-tiny",
        local_dir=str(model_dir),
        allow_patterns=["*.json", "model.safetensors"],
    )
    print(f"Model downloaded to {model_dir}", flush=True)


def track(args):
    import numpy as np
    import torch
    from PIL import Image
    from transformers import Sam2VideoModel, Sam2VideoProcessor

    work = args.masks.parent
    frames_dir = work / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    args.masks.mkdir(parents=True, exist_ok=True)
    duration = float(args.duration)
    start, end = float(args.start), float(args.end)
    span = end - start
    fps = int(args.sample_fps)
    frame_pattern = frames_dir / "%06d.jpg"
    decoded = subprocess.run(
        [str(args.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t", str(span),
         "-i", str(args.video), "-vf", "fps=" + str(fps) + ",scale=1024:1024:force_original_aspect_ratio=decrease",
         "-q:v", "3", str(frame_pattern)],
        capture_output=True, text=True,
    )
    if decoded.returncode:
        raise RuntimeError(decoded.stderr[-1200:] or "Could not decode frames for SAM 2.")
    frame_paths = sorted(frames_dir.glob("*.jpg"))
    if len(frame_paths) < 2:
        raise RuntimeError("SAM 2 needs at least two sampled frames in the selected range.")
    frames = [Image.open(path).convert("RGB") for path in frame_paths]
    frame_width, frame_height = frames[0].size
    seed_idx = max(0, min(len(frames) - 1, int(round((float(args.seed_time) - start) * fps))))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device != "cuda":
        raise RuntimeError("CUDA недоступна: SAM 2 должна запускаться последовательно на RTX 5080.")
    dtype = torch.bfloat16
    model = Sam2VideoModel.from_pretrained(str(args.model_dir), local_files_only=True).to(device, dtype=dtype).eval()
    processor = Sam2VideoProcessor.from_pretrained(str(args.model_dir), local_files_only=True)
    session = processor.init_video_session(
        video=frames,
        inference_device=device,
        inference_state_device=device,
        processing_device="cpu",
        video_storage_device="cpu",
        max_vision_features_cache_size=1,
        dtype=dtype,
    )
    del frames
    gc.collect()

    px = float(args.x) * frame_width
    py = float(args.y) * frame_height
    processor.add_inputs_to_inference_session(
        inference_session=session,
        frame_idx=seed_idx,
        obj_ids=1,
        input_points=[[[[px, py]]]],
        input_labels=[[[1]]],
        original_size=(frame_height, frame_width),
    )
    tracked = {}

    def store(output):
        masks = processor.post_process_masks(
            [output.pred_masks],
            original_sizes=[[session.video_height, session.video_width]],
            binarize=False,
        )[0]
        mask = masks[0, 0].float().cpu().numpy()
        # The SAM decoder returns logits; a zero threshold selects the foreground.
        binary = np.where(mask > 0, 255, 0).astype(np.uint8)
        tracked[int(output.frame_idx)] = binary

    with torch.inference_mode(), torch.autocast("cuda", dtype=dtype):
        first = model(inference_session=session, frame_idx=seed_idx)
        store(first)
        for reverse in (False, True):
            iterator = model.propagate_in_video_iterator(
                session,
                start_frame_idx=seed_idx,
                max_frame_num_to_track=(seed_idx if reverse else len(frame_paths) - seed_idx - 1),
                reverse=reverse,
                show_progress_bar=False,
            )
            for output in iterator:
                store(output)
                if len(tracked) % 12 == 0:
                    pct = min(70, 12 + int(len(tracked) * 58 / max(1, len(frame_paths))))
                    print(f"{pct}% Отслежено кадров: {len(tracked)}/{len(frame_paths)}", flush=True)

    if len(tracked) < max(2, int(len(frame_paths) * .65)):
        raise RuntimeError("SAM 2 потеряла объект на большей части фрагмента. Выберите другой кадр или точку.")

    full_count = max(1, int(math.ceil(duration * fps)))
    blank = np.zeros((frame_height, frame_width), dtype=np.uint8)
    for idx in range(full_count):
        Image.fromarray(blank, mode="L").save(args.masks / f"{idx:08d}.png")
    offset = int(round(start * fps))
    for frame_idx, mask in tracked.items():
        target_idx = offset + frame_idx
        if 0 <= target_idx < full_count:
            Image.fromarray(mask, mode="L").save(args.masks / f"{target_idx:08d}.png")
    print(f"78% Маска сохранена: {len(tracked)} кадров, {fps} кадр/с", flush=True)
    del model, processor, session, tracked
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--masks", type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float, default=0)
    parser.add_argument("--seed-time", type=float, default=0)
    parser.add_argument("--duration", type=float, default=0)
    parser.add_argument("--x", type=float, default=.5)
    parser.add_argument("--y", type=float, default=.5)
    parser.add_argument("--sample-fps", type=int, default=8)
    args = parser.parse_args()
    if args.install:
        install(args.model_dir)
    else:
        for key in ("video", "masks", "ffmpeg"):
            if getattr(args, key) is None:
                parser.error(f"--{key} is required for tracking")
        track(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise
