import os
import sys
from pathlib import Path
import numpy as np
import pytest
from PIL import Image

# Ensure skyedge/pi is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ai.segmentation_detector import SegmentationDetector


@pytest.fixture
def sample_image(tmp_path):
    """Creates a temporary test image."""
    img_path = tmp_path / "test_frame.jpg"
    img = Image.new("RGB", (64, 64), color=(100, 100, 100))
    img.save(img_path)
    return str(img_path)


def test_segmentation_detector_no_model_fallback(sample_image):
    """
    When no model path is provided (or model file is absent),
    SegmentationDetector should set session = None and predict()
    must return ('routine', 0.0, 0.0).
    """
    detector = SegmentationDetector(model_path="non_existent_model_dir/model.onnx")
    assert detector.session is None

    event_type, confidence, severity = detector.predict(sample_image)
    assert event_type == "routine"
    assert confidence == 0.0
    assert severity == 0.0


def test_segmentation_detector_noise_threshold_below(sample_image):
    """
    When landslide pixel fraction is BELOW noise_threshold (e.g. 0.01 vs 0.02),
    it should be filtered out as noise and return ('routine', 0.0, 0.0).
    """
    detector = SegmentationDetector(model_path="non_existent_model.onnx", noise_threshold=0.02)

    # Mock an ONNX session that returns a small cluster of landslide pixels (1% of 64x64)
    class MockSessionBelowThreshold:
        def get_inputs(self):
            class InputMeta:
                name = "pixel_values"
            return [InputMeta()]

        def run(self, output_names, input_feed):
            H, W = 64, 64
            # Class 0: background (higher logits), Class 1: landslide (mostly lower)
            logits = np.zeros((1, 2, H, W), dtype=np.float32)
            logits[0, 0, :, :] = 2.0  # default background high
            # Set only 1% of pixels to landslide
            landslide_pixel_count = int(0.01 * H * W)
            logits[0, 1, 0, :landslide_pixel_count] = 5.0
            return [logits]

    detector.session = MockSessionBelowThreshold()
    detector.input_name = "pixel_values"

    event_type, confidence, severity = detector.predict(sample_image)
    assert event_type == "routine"
    assert confidence == 0.0
    assert severity == 0.0


def test_segmentation_detector_noise_threshold_above(sample_image):
    """
    When landslide pixel fraction is ABOVE noise_threshold (e.g. 0.15 vs 0.02),
    it should return ('landslide', confidence, severity) with non-zero values.
    """
    detector = SegmentationDetector(model_path="non_existent_model.onnx", noise_threshold=0.02)

    # Mock an ONNX session that returns 15% landslide pixels
    class MockSessionAboveThreshold:
        def get_inputs(self):
            class InputMeta:
                name = "pixel_values"
            return [InputMeta()]

        def run(self, output_names, input_feed):
            H, W = 64, 64
            # Class 0: background (1.0), Class 1: landslide (4.0 for 15% of pixels)
            flat_logits = np.zeros((1, 2, H * W), dtype=np.float32)
            flat_logits[0, 0, :] = 1.0
            landslide_pixel_count = int(0.15 * H * W)
            flat_logits[0, 1, :landslide_pixel_count] = 4.0
            logits = flat_logits.reshape((1, 2, H, W))
            return [logits]

    detector.session = MockSessionAboveThreshold()
    detector.input_name = "pixel_values"

    event_type, confidence, severity = detector.predict(sample_image)
    assert event_type == "landslide"
    assert confidence > 0.5
    assert severity >= 0.10
