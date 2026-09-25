def compute_priority(severity, confidence, urgency, mission_value, weights, thresholds):
    score = (weights["severity"]*severity + weights["confidence"]*confidence +
              weights["urgency"]*urgency + weights["mission_value"]*mission_value) * 100
    if score >= thresholds["transmit_now"]: action = "TRANSMIT_NOW"
    elif score >= thresholds["compress_and_queue"]: action = "COMPRESS_AND_QUEUE"
    elif score >= thresholds["queue_or_store"]: action = "QUEUE_OR_STORE"
    else: action = "STORE_OR_DISCARD"
    return round(score, 2), action
