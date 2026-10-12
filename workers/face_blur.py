"""Privacy face blur worker using OpenCV's tiny YuNet face detector."""
import argparse
import subprocess
import sys
from pathlib import Path


def _blur_roi(frame, bounds, cv2, np, mode):
    x1, y1, x2, y2 = bounds
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return
    height, width = roi.shape[:2]
    if mode == "pixelate":
        tiny = cv2.resize(roi, (max(1, width // 14), max(1, height // 14)), interpolation=cv2.INTER_LINEAR)
        blurred = cv2.resize(tiny, (width, height), interpolation=cv2.INTER_NEAREST)
    else:
        sigma = max(12.0, min(width, height) * 0.22)
        blurred = cv2.GaussianBlur(roi, (0, 0), sigmaX=sigma, sigmaY=sigma)

    # A feathered oval avoids a hard rectangular patch while covering the full face.
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.ellipse(mask, (width // 2, height // 2), (max(1, width // 2), max(1, height // 2)),
                0, 0, 360, 255, -1)
    feather = max(2, int(min(width, height) * 0.10))
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=feather, sigmaY=feather).astype(np.float32) / 255.0
    alpha = mask[:, :, None]
    frame[y1:y2, x1:x2] = (blurred.astype(np.float32) * alpha + roi.astype(np.float32) * (1.0 - alpha)).astype(np.uint8)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--mode", choices=("gaussian", "pixelate"), default="gaussian")
    args = parser.parse_args()

    import cv2
    import numpy as np

    if hasattr(cv2, "FaceDetectorYN_create"):
        detector_factory = cv2.FaceDetectorYN_create
    elif hasattr(cv2, "FaceDetectorYN") and hasattr(cv2.FaceDetectorYN, "create"):
        detector_factory = cv2.FaceDetectorYN.create
    else:
        raise RuntimeError("This OpenCV build does not include FaceDetectorYN.")

    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        raise RuntimeError("Could not open the selected video.")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width -= width % 2
    height -= height % 2
    fps = fps if 1.0 <= fps <= 120.0 else 30.0
    if width < 2 or height < 2:
        cap.release()
        raise RuntimeError("The selected video has invalid frame dimensions.")

    scale = min(1.0, 960.0 / max(width, height))
    detect_w = max(32, ((int(width * scale) + 31) // 32) * 32)
    detect_h = max(32, ((int(height * scale) + 31) // 32) * 32)
    detector = detector_factory(str(args.model), "", (detect_w, detect_h), 0.62, 0.30, 5000)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [str(args.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
               "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "pipe:0",
               "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(out_path)]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    frame_no = 0
    face_count = 0
    progress_interval = max(1, total // 100) if total else 60
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame.shape[1] != width or frame.shape[0] != height:
                frame = frame[:height, :width]
            detection_frame = cv2.resize(frame, (detect_w, detect_h), interpolation=cv2.INTER_LINEAR)
            detector.setInputSize((detect_w, detect_h))
            _, faces = detector.detect(detection_frame)
            if faces is not None:
                sx, sy = width / detect_w, height / detect_h
                for face in faces:
                    if float(face[14]) < 0.62:
                        continue
                    x, y, box_w, box_h = (float(v) for v in face[:4])
                    # Expand around the face to include forehead, ears and chin.
                    x1 = max(0, int((x - box_w * 0.22) * sx))
                    y1 = max(0, int((y - box_h * 0.28) * sy))
                    x2 = min(width, int((x + box_w * 1.22) * sx))
                    y2 = min(height, int((y + box_h * 1.30) * sy))
                    _blur_roi(frame, (x1, y1, x2, y2), cv2, np, args.mode)
                    face_count += 1
            try:
                encoder.stdin.write(frame.tobytes())
            except (BrokenPipeError, OSError):
                break
            frame_no += 1
            if frame_no % progress_interval == 0:
                pct = min(99, int(frame_no * 100 / total)) if total else 0
                print(f"Размытие лиц… {pct}%", flush=True)
    finally:
        cap.release()
        if encoder.stdin and not encoder.stdin.closed:
            encoder.stdin.close()
    stderr = encoder.stderr.read().decode("utf-8", errors="replace") if encoder.stderr else ""
    code = encoder.wait()
    if code:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(stderr[-1200:] or f"FFmpeg exited with code {code}.")
    if frame_no == 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError("No video frames were decoded.")
    if face_count == 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError("YuNet did not detect a face. No unblurred copy was exported.")
    print("Размытие лиц завершено: {} кадров, {} обнаружений.".format(frame_no, face_count), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise
