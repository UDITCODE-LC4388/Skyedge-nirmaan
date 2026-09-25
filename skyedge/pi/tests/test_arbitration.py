import sys
from pathlib import Path
import pytest

# Ensure skyedge/pi is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from priority.arbitration import arbitrate


def test_arbitration_yolo_wins():
    """
    When YOLO detector priority score is higher than SegFormer,
    YOLO must win arbitration and its incident details must be selected.
    """
    winner, ev_type, conf, sev, score, action = arbitrate(
        det_event="wildfire",
        det_conf=0.92,
        det_sev=0.85,
        det_score=91.5,
        det_action="TRANSMIT_NOW",
        seg_event="routine",
        seg_conf=0.0,
        seg_sev=0.0,
        seg_score=6.5,
        seg_action="STORE_OR_DISCARD",
        tie_breaker_threshold=0.0,
    )
    assert winner == "YOLO"
    assert ev_type == "wildfire"
    assert conf == 0.92
    assert sev == 0.85
    assert score == 91.5
    assert action == "TRANSMIT_NOW"


def test_arbitration_segformer_wins():
    """
    When SegFormer segmentation priority score is higher than YOLO,
    SegFormer must win arbitration and its landslide details must be selected.
    """
    winner, ev_type, conf, sev, score, action = arbitrate(
        det_event="routine",
        det_conf=0.0,
        det_sev=0.0,
        det_score=6.5,
        det_action="STORE_OR_DISCARD",
        seg_event="landslide",
        seg_conf=0.88,
        seg_sev=0.60,
        seg_score=78.2,
        seg_action="COMPRESS_AND_QUEUE",
        tie_breaker_threshold=0.0,
    )
    assert winner == "SegFormer"
    assert ev_type == "landslide"
    assert conf == 0.88
    assert sev == 0.60
    assert score == 78.2
    assert action == "COMPRESS_AND_QUEUE"


def test_arbitration_tie_resolves_to_yolo():
    """
    When both models produce identical priority scores,
    arbitration must break the tie in favor of YOLO detector.
    """
    winner, ev_type, conf, sev, score, action = arbitrate(
        det_event="flood",
        det_conf=0.75,
        det_sev=0.70,
        det_score=75.0,
        det_action="COMPRESS_AND_QUEUE",
        seg_event="landslide",
        seg_conf=0.80,
        seg_sev=0.65,
        seg_score=75.0,
        seg_action="COMPRESS_AND_QUEUE",
        tie_breaker_threshold=0.0,
    )
    assert winner == "YOLO"
    assert ev_type == "flood"
    assert conf == 0.75
    assert score == 75.0


def test_arbitration_tie_breaker_threshold():
    """
    With a non-zero tie_breaker_threshold, SegFormer only wins if its score
    exceeds YOLO by strictly more than that threshold.
    """
    # Difference is 1.0, which does NOT exceed threshold 2.0 -> YOLO wins
    winner1, ev1, _, _, score1, _ = arbitrate(
        det_event="fire",
        det_conf=0.70,
        det_sev=0.70,
        det_score=70.0,
        det_action="COMPRESS_AND_QUEUE",
        seg_event="landslide",
        seg_conf=0.75,
        seg_sev=0.70,
        seg_score=71.0,
        seg_action="COMPRESS_AND_QUEUE",
        tie_breaker_threshold=2.0,
    )
    assert winner1 == "YOLO"
    assert ev1 == "fire"
    assert score1 == 70.0

    # Difference is 3.0, which EXCEEDS threshold 2.0 -> SegFormer wins
    winner2, ev2, _, _, score2, _ = arbitrate(
        det_event="fire",
        det_conf=0.70,
        det_sev=0.70,
        det_score=70.0,
        det_action="COMPRESS_AND_QUEUE",
        seg_event="landslide",
        seg_conf=0.85,
        seg_sev=0.70,
        seg_score=73.0,
        seg_action="COMPRESS_AND_QUEUE",
        tie_breaker_threshold=2.0,
    )
    assert winner2 == "SegFormer"
    assert ev2 == "landslide"
    assert score2 == 73.0
