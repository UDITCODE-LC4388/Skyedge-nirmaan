import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def generate_sample():
    config_path = BASE_DIR / "config.yaml"
    fallback_rel = "ai/test_assets/sample_frame.jpg"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            fallback_rel = cfg.get("camera", {}).get("fallback_test_image", fallback_rel)

    out_path = Path(fallback_rel)
    if not out_path.is_absolute():
        out_path = BASE_DIR / out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate synthetic 320x320 RGB image
    width, height = 320, 320
    img = Image.new("RGB", (width, height), color=(34, 139, 34))  # Forest green ground
    draw = ImageDraw.Draw(img)

    # Simulated river / water body
    draw.polygon([(0, 200), (320, 260), (320, 320), (0, 320)], fill=(30, 60, 150))

    # Simulated fire hotspot
    draw.ellipse([(100, 80), (180, 160)], fill=(255, 69, 0))
    draw.ellipse([(120, 95), (160, 145)], fill=(255, 215, 0))

    # Simulated smoke plume
    draw.ellipse([(140, 40), (240, 120)], fill=(128, 128, 128))
    draw.ellipse([(180, 20), (280, 90)], fill=(169, 169, 169))

    img.save(out_path, "JPEG", quality=95)
    print(f"Generated synthetic 320x320 test JPEG at: {out_path}")
    return out_path


if __name__ == "__main__":
    generate_sample()
