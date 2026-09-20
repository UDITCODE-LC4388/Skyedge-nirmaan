import datetime


class OLEDStatus:
    def __init__(self, port=1, address=0x3C):
        self.device = None
        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306

            serial = i2c(port=port, address=address)
            self.device = ssd1306(serial)
        except Exception:
            self.device = None

    def update(self, score, action, telemetry, bandwidth, storage_pct):
        if self.device is not None:
            try:
                from luma.core.render import canvas

                with canvas(self.device) as draw:
                    draw.text((0, 0), f"Score: {score}", fill="white")
                    draw.text((0, 16), f"Act: {action}", fill="white")
                    draw.text((0, 32), f"BW: {bandwidth} Stg: {storage_pct}%", fill="white")
                    v = telemetry.get("voltage", "--") if isinstance(telemetry, dict) else "--"
                    draw.text((0, 48), f"Volt: {v}V", fill="white")
            except Exception:
                pass

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] score={score} action={action} bandwidth={bandwidth} storage_pct={storage_pct}% telemetry={telemetry}")
