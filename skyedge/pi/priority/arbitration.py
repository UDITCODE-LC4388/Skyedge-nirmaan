def arbitrate(
    det_event, det_conf, det_sev, det_score, det_action,
    seg_event, seg_conf, seg_sev, seg_score, seg_action,
    tie_breaker_threshold=0.0
):
    """
    Arbitrates between YOLO Detector and SegFormer Segmentation Detector.
    Returns: (winner, event_type, confidence, severity, priority_score, base_action)
    where winner is 'SegFormer' if (seg_score - det_score) > tie_breaker_threshold, else 'YOLO'.
    A tie (or seg_score <= det_score + tie_breaker_threshold) always resolves in favor of YOLO.
    """
    if (seg_score - det_score) > tie_breaker_threshold:
        return "SegFormer", seg_event, seg_conf, seg_sev, seg_score, seg_action
    else:
        return "YOLO", det_event, det_conf, det_sev, det_score, det_action
