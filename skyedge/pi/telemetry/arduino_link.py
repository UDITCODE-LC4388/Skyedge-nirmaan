class ArduinoLink:
    def __init__(self, port, baud):
        try:
            import serial
            self.ser = serial.Serial(port, baud, timeout=2)
        except Exception:
            self.ser = None

    def read(self):
        if self.ser:
            import json
            return json.loads(self.ser.readline().decode().strip())
        import random
        return {
            "voltage": round(random.uniform(4.6, 5.1), 2),
            "current": round(random.uniform(0.1, 0.3), 2),
            "temperature": round(random.uniform(20.0, 35.0), 1),
            "illumination": random.choice([0, 1]),
            "accel_x": round(random.uniform(-1.0, 1.0), 2),
            "accel_y": round(random.uniform(-1.0, 1.0), 2),
            "accel_z": round(random.uniform(9.5, 10.0), 2),
            "storage_pct": random.randint(10, 90),
            "bandwidth": random.choice(["HIGH", "MEDIUM", "LOW", "CRITICAL"]),
        }
