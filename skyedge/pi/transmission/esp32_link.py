class ESP32Link:
    def __init__(self, port, baud):
        try:
            import serial
            self.ser = serial.Serial(port, baud, timeout=2)
        except Exception:
            self.ser = None

    def send(self, packet_path, score, event_type):
        if self.ser:
            import json
            payload = json.dumps({
                "packet_path": packet_path,
                "score": score,
                "event_type": event_type,
            })
            self.ser.write((payload + "\n").encode())
            return
        print(f"[SIMULATED TX] {event_type} score={score} packet={packet_path}")
