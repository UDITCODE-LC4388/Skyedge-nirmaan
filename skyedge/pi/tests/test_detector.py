import sys
from pathlib import Path
import pytest
import yaml

# Ensure skyedge/pi is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ai.detector import Detector


@pytest.fixture(scope="module")
def cfg():
    config_path = BASE_DIR / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_detector_simulation_fallback(cfg, capsys):
    # 1. Load event_classes from config.yaml's mission_value keys
    event_classes = list(cfg["mission_value"].keys())
    model_path = cfg["ai"]["model_path"]

    # 2. Instantiate Detector with a nonexistent model_path to test simulated fallback
    detector = Detector(model_path="nonexistent_model.pt", event_classes=event_classes)

    # Confirms it falls back to simulated mode without crashing
    assert detector.model is None

    # 3. Calls predict() on any dummy image path
    dummy_path = "storage/incoming/dummy_frame.jpg"
    result = detector.predict(dummy_path)

    # Asserts 3-tuple where:
    # - event_type is one of event_classes
    # - confidence is a float between 0 and 1
    # - severity is a float between 0 and 1
    assert isinstance(result, tuple)
    assert len(result) == 3

    event_type, confidence, severity = result

    assert event_type in event_classes
    assert isinstance(confidence, float)
    assert 0.0 <= confidence <= 1.0
    assert isinstance(severity, float)
    assert 0.0 <= severity <= 1.0

    # Confirms [SIMULATED DETECTION] output
    captured = capsys.readouterr()
    assert "[SIMULATED DETECTION]" in captured.out


def test_detector_real_model_routine(cfg):
    event_classes = list(cfg["mission_value"].keys())
    model_path = cfg["ai"]["model_path"]
    detector = Detector(model_path=model_path, event_classes=event_classes)
    assert detector.model is not None

    # Test on synthetic sample frame (0 detections)
    event_type, confidence, severity = detector.predict("ai/test_assets/sample_frame.jpg")
    assert event_type == "routine"
    assert confidence == 0.0
    assert severity == 0.0

