"""Sparse, CPU-only YuNet face tracking used to keep a speaker inside an export crop."""
import argparse
import json
import math
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--crop-output")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--crop-width", type=int)
    parser.add_argument("--crop-height", type=int)
    parser.add_argument("--sample-fps", type=float, default=1.0)
    args = parser.parse_args()

    import cv2

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise RuntimeError("Не удалось открыть видео для отслеживания лица.")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if width < 2 or height < 2 or not 1 <= fps <= 120:
        cap.release()
        raise RuntimeError("У видео некорректные размеры или частота кадров.")

    scale = min(1.0, 960.0 / max(width, height))
    detect_w = max(32, int(width * scale / 32) * 32)
    detect_h = max(32, int(height * scale / 32) * 32)
    detector_factory = getattr(cv2, "FaceDetectorYN_create", None)
    if detector_factory is None and hasattr(cv2, "FaceDetectorYN"):
        detector_factory = cv2.FaceDetectorYN.create
    if detector_factory is None:
        cap.release()
        raise RuntimeError("Установленная сборка OpenCV не содержит YuNet FaceDetectorYN.")
    detector = detector_factory(str(args.model), "", (detect_w, detect_h), 0.62, 0.30, 5000)
    interval = max(1, round(fps / max(0.2, min(2.0, args.sample_fps))))
    points = []
    previous = None
    smoothed_x = None
    frame_no = 0
    next_report = 10
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_no % interval == 0:
                sample = cv2.resize(frame, (detect_w, detect_h), interpolation=cv2.INTER_AREA)
                _, faces = detector.detect(sample)
                candidates = []
                if faces is not None:
                    for face in faces:
                        confidence = float(face[14])
                        box_w, box_h = float(face[2]), float(face[3])
                        if confidence < 0.62 or box_w < 12 or box_h < 12:
                            continue
                        x = max(0.0, min(1.0, (float(face[0]) + box_w / 2) / detect_w))
                        area = box_w * box_h / (detect_w * detect_h)
                        candidates.append({"x": x, "area": area, "confidence": confidence})
                if candidates:
                    if previous is None:
                        chosen = max(candidates, key=lambda item: item["area"] * item["confidence"])
                    else:
                        nearby = [item for item in candidates if abs(item["x"] - previous) <= 0.4]
                        pool = nearby or candidates
                        chosen = max(pool, key=lambda item: 0.45 * item["confidence"] +
                                     0.35 * math.sqrt(item["area"]) - 0.55 * abs(item["x"] - previous))
                    alpha = 0.5 if smoothed_x is None else 0.42
                    smoothed_x = chosen["x"] if smoothed_x is None else smoothed_x + alpha * (chosen["x"] - smoothed_x)
                    previous = chosen["x"]
                    points.append({"t": round(frame_no / fps, 3), "x": round(smoothed_x, 5),
                                   "confidence": round(chosen["confidence"], 3)})
            frame_no += 1
            if total and frame_no * 50 // total >= next_report:
                print(f"Анализ положения говорящего… {next_report}%", flush=True)
                next_report += 10
    finally:
        cap.release()

    crop_created = False
    if points and args.crop_output:
        if not args.ffmpeg or not args.crop_width or not args.crop_height:
            raise ValueError("Для создания вертикального кадра нужны FFmpeg и размеры экспорта.")
        target_w = max(2, args.crop_width - args.crop_width % 2)
        target_h = max(2, args.crop_height - args.crop_height % 2)
        crop_w = max(2, min(width, int(height * target_w / target_h / 2 + .5) * 2))
        destination = Path(args.crop_output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(args.video)
        command = [str(args.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
                   "-pix_fmt", "bgr24", "-s", f"{target_w}x{target_h}", "-r", f"{fps:.6f}", "-i", "pipe:0",
                   "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart", str(destination)]
        encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        frame_no = 0
        crop_report = 60
        try:
            if not cap.isOpened():
                raise RuntimeError("Не удалось повторно открыть видео для кадрирования.")
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                at = frame_no / fps
                if at <= points[0]["t"]:
                    center_x = points[0]["x"]
                elif at >= points[-1]["t"]:
                    center_x = points[-1]["x"]
                else:
                    for left, right in zip(points, points[1:]):
                        if at <= right["t"]:
                            span = max(.001, right["t"] - left["t"])
                            ratio = (at - left["t"]) / span
                            center_x = left["x"] + (right["x"] - left["x"]) * ratio
                            break
                left_x = int(round(center_x * width - crop_w / 2))
                left_x = max(0, min(width - crop_w, left_x))
                left_x -= left_x % 2
                crop = frame[:, left_x:left_x + crop_w]
                resized = cv2.resize(crop, (target_w, target_h), interpolation=cv2.INTER_AREA)
                try:
                    encoder.stdin.write(resized.tobytes())
                except (BrokenPipeError, OSError):
                    break
                frame_no += 1
                pct = 50 + (frame_no * 50 // total if total else 50)
                if pct >= crop_report:
                    print(f"Кадрирую по траектории лица… {crop_report}%", flush=True)
                    crop_report += 10
        finally:
            cap.release()
            if encoder.stdin and not encoder.stdin.closed:
                encoder.stdin.close()
        stderr = encoder.stderr.read().decode("utf-8", errors="replace") if encoder.stderr else ""
        code = encoder.wait()
        if code or frame_no == 0:
            destination.unlink(missing_ok=True)
            raise RuntimeError(stderr[-1200:] or "FFmpeg не создал кадрированное видео.")
        crop_created = True

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"available": True, "detected": bool(points), "crop_available": crop_created,
                                  "width": width, "height": height, "duration": round(frame_no / fps, 3),
                                  "sample_fps": round(fps / interval, 3), "points": points},
                                 ensure_ascii=False), encoding="utf-8")
    print(f"Кадрирование: найдено {len(points)} контрольных точек лица.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), flush=True)
        raise
