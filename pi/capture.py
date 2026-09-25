import os
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def capture(cfg):
    """
    Tries libcamera-jpeg to capture a real photo to camera.capture_path.
    If that fails (no camera attached or tool missing), copies fallback_test_image
    to capture_path instead and prints a line noting fallback was used.
    Returns capture_path either way.
    """
    camera_cfg = cfg.get("camera", {})
    capture_path = camera_cfg.get("capture_path", "storage/incoming/frame.jpg")
    fallback_image = camera_cfg.get("fallback_test_image", "ai/test_assets/sample_frame.jpg")
    timeout_ms = camera_cfg.get("capture_timeout_ms", 1000)

    # Resolve paths relative to skyedge/pi if relative
    cap_path = Path(capture_path)
    resolved_cap = cap_path if cap_path.is_absolute() else BASE_DIR / cap_path

    fb_path = Path(fallback_image)
    resolved_fb = fb_path if fb_path.is_absolute() else BASE_DIR / fb_path

    resolved_cap.parent.mkdir(parents=True, exist_ok=True)

    captured = False
    try:
        cmd = [
            "libcamera-jpeg",
            "-o", str(resolved_cap),
            "-t", str(timeout_ms),
            "-n",
        ]
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(2.0, timeout_ms / 1000.0 + 2.0),
        )
        if res.returncode == 0 and resolved_cap.exists() and resolved_cap.stat().st_size > 0:
            captured = True
    except Exception:
        captured = False

    if not captured:
        if resolved_fb.exists():
            shutil.copy2(str(resolved_fb), str(resolved_cap))
        else:
            # Create a simple fallback if sample_frame does not exist
            from PIL import Image
            fallback_img = Image.new("RGB", (320, 320), color=(100, 150, 200))
            resolved_fb.parent.mkdir(parents=True, exist_ok=True)
            fallback_img.save(resolved_fb, "JPEG")
            shutil.copy2(str(resolved_fb), str(resolved_cap))

        print(f"[CAPTURE] libcamera-jpeg failed (no camera attached); using fallback image: {fallback_image} -> {capture_path}")

    return capture_path
