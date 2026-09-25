import sys
from pathlib import Path
import pytest
import yaml

# Ensure skyedge/pi is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from resource.manager import ResourceManager


@pytest.fixture(scope="module")
def cfg():
    config_path = BASE_DIR / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_critical_bandwidth_low_score_overrides_to_queue_or_store(cfg):
    # 1. bandwidth is CRITICAL and score is 50 (low, < transmit_now threshold)
    # -> decide() should override to QUEUE_OR_STORE
    rm = ResourceManager(cfg)
    rm.update({"bandwidth": "CRITICAL"})

    score = 50
    transmit_now_threshold = cfg["thresholds"]["transmit_now"]
    assert score < transmit_now_threshold

    result = rm.decide(priority_score=score, base_action="COMPRESS_AND_QUEUE")
    assert result == "QUEUE_OR_STORE"


def test_critical_bandwidth_high_score_passes_through(cfg):
    # 2. bandwidth is CRITICAL and score is 95 (very high, >= transmit_now threshold)
    # -> decide() should NOT override, base_action passes through unchanged
    rm = ResourceManager(cfg)
    rm.update({"bandwidth": "CRITICAL"})

    score = 95
    transmit_now_threshold = cfg["thresholds"]["transmit_now"]
    assert score >= transmit_now_threshold

    result = rm.decide(priority_score=score, base_action="TRANSMIT_NOW")
    assert result == "TRANSMIT_NOW"


def test_storage_limit_exceeded_low_score_overrides_to_store_or_discard(cfg):
    # 3. storage_pct is at/above the configured storage_limit_pct and score is 20 (low, < queue_or_store threshold)
    # -> decide() should override to STORE_OR_DISCARD
    rm = ResourceManager(cfg)
    storage_limit = cfg["resources"]["storage_limit_pct"]
    rm.update({"storage_pct": storage_limit})

    score = 20
    queue_or_store_threshold = cfg["thresholds"]["queue_or_store"]
    assert score < queue_or_store_threshold

    result = rm.decide(priority_score=score, base_action="QUEUE_OR_STORE")
    assert result == "STORE_OR_DISCARD"


def test_high_bandwidth_low_storage_returns_base_action_unchanged(cfg):
    # 4. bandwidth is HIGH and storage is low (< storage_limit_pct)
    # -> decide() should just return base_action unchanged
    rm = ResourceManager(cfg)
    storage_limit = cfg["resources"]["storage_limit_pct"]
    rm.update({"bandwidth": "HIGH", "storage_pct": storage_limit // 2})

    for base_action in ["TRANSMIT_NOW", "COMPRESS_AND_QUEUE", "QUEUE_OR_STORE"]:
        result = rm.decide(priority_score=75, base_action=base_action)
        assert result == base_action
