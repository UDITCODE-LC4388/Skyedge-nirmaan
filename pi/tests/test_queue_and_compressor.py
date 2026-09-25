import os
import sys
from pathlib import Path
import cv2
import pytest
from PIL import Image
import yaml

# Ensure skyedge/pi is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from transmission.compressor import compress
from transmission.queue import PriorityQueue


@pytest.fixture(scope="module")
def cfg():
    config_path = BASE_DIR / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_priority_queue():
    # Pushes 3 items with scores 50, 90, 70 and asserts pop() returns them in order 90, 70, 50
    pq = PriorityQueue()
    pq.push(50, "item_50")
    pq.push(90, "item_90")
    pq.push(70, "item_70")

    assert pq.pop() == "item_90"
    assert pq.pop() == "item_70"
    assert pq.pop() == "item_50"
    assert pq.pop() is None


def test_compress_image(cfg, tmp_path):
    # 1. Use Pillow to generate a small synthetic test image (200x200 solid-color PNG) in a temp directory
    png_path = tmp_path / "synthetic.png"
    img = Image.new("RGB", (200, 200), color=(30, 144, 255))
    img.save(png_path, "PNG")

    # 2. Convert and save it as a .jpg
    jpg_path = tmp_path / "synthetic.jpg"
    with Image.open(png_path) as im:
        im.convert("RGB").save(jpg_path, "JPEG")

    # 3. Load queued_quality and storage.queued path from config.yaml
    queued_quality = cfg["compression"]["queued_quality"]
    output_dir = str(BASE_DIR / cfg["storage"]["queued"])

    # 4. Call compress()
    output_path = compress(str(jpg_path), queued_quality, output_dir)

    try:
        # 5. Assert output file exists and can be re-opened as a valid image with OpenCV
        assert os.path.exists(output_path)
        loaded_img = cv2.imread(output_path)
        assert loaded_img is not None
        assert loaded_img.shape == (200, 200, 3)
    finally:
        if os.path.exists(output_path):
            os.remove(output_path)
