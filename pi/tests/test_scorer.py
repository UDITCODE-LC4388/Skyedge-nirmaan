import sys
from pathlib import Path
import pytest
import yaml

# Ensure skyedge/pi is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from priority.scorer import compute_priority


@pytest.fixture(scope="module")
def config():
    config_path = BASE_DIR / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def weights(config):
    return config["weights"]


@pytest.fixture
def thresholds(config):
    return config["thresholds"]


def test_transmit_now_boundary(weights, thresholds):
    # Score lands just above 90 (91.1)
    score, action = compute_priority(
        severity=0.92,
        confidence=0.90,
        urgency=0.90,
        mission_value=0.92,
        weights=weights,
        thresholds=thresholds,
    )
    assert score > 90
    assert action == "TRANSMIT_NOW"


def test_compress_and_queue_boundary(weights, thresholds):
    # Score lands just above 70 (71.1)
    score, action = compute_priority(
        severity=0.72,
        confidence=0.70,
        urgency=0.70,
        mission_value=0.72,
        weights=weights,
        thresholds=thresholds,
    )
    assert 70 <= score < 90
    assert score > 70
    assert action == "COMPRESS_AND_QUEUE"


def test_queue_or_store_boundary(weights, thresholds):
    # Score lands just above 40 (41.1)
    score, action = compute_priority(
        severity=0.42,
        confidence=0.40,
        urgency=0.40,
        mission_value=0.42,
        weights=weights,
        thresholds=thresholds,
    )
    assert 40 <= score < 70
    assert score > 40
    assert action == "QUEUE_OR_STORE"


def test_store_or_discard_low_score(weights, thresholds):
    # Score lands below 40 (20.0)
    score, action = compute_priority(
        severity=0.20,
        confidence=0.20,
        urgency=0.20,
        mission_value=0.20,
        weights=weights,
        thresholds=thresholds,
    )
    assert score < 40
    assert action == "STORE_OR_DISCARD"
