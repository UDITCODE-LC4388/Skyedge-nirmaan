class ResourceManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.bandwidth = "HIGH"
        self.storage_pct = 0

    def update(self, telemetry):
        self.storage_pct = telemetry.get("storage_pct", self.storage_pct)
        self.bandwidth = telemetry.get("bandwidth", self.bandwidth)

    def decide(self, priority_score, base_action):
        if self.bandwidth == "CRITICAL" and priority_score < self.cfg["thresholds"]["transmit_now"] and base_action != "STORE_OR_DISCARD":
            return "QUEUE_OR_STORE"
        if self.storage_pct >= self.cfg["resources"]["storage_limit_pct"] and priority_score < self.cfg["thresholds"]["queue_or_store"]:
            return "STORE_OR_DISCARD"
        return base_action
