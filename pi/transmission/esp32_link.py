import base64
import os
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from PIL import Image
    import io
except ImportError:
    Image = None


class ESP32Link:
    def __init__(self, port, baud):
        self.ser = None
        if port and str(port).lower() != "none":
            try:
                import serial
                self.ser = serial.Serial(port, baud, timeout=2)
            except Exception:
                self.ser = None

    def send(
        self,
        packet_path,
        score,
        event_type,
        confidence=0.0,
        severity=0.0,
        urgency=0.0,
        mission_value=0.0,
        action="TRANSMIT_NOW",
        justification="",
        target_resolution=(160, 120),
        quality=60,
    ):
        """
        Transmits detection data and base64-encoded image over UART to ESP32 #1 (Downlink node).

        Protocol:
          Line 1: EVT:<event_type>|<score>|<justification>
          Line 2: IMG:<base64_jpeg_data>

        ESP32 #1 assigns the monotonic decision sequence `image_id` and chunks
        the transmission into HDR / DAT / END packets over ESP-NOW.
        """
        # Construct real justification if not explicitly passed
        if not justification:
            justification = (
                f"event={event_type} conf={confidence:.2f} sev={severity:.2f} "
                f"urg={urgency:.2f} mv={mission_value:.2f} score={score:.2f} act={action}"
            )

        path_obj = Path(packet_path)
        b64_data = ""
        jpeg_len = 0

        if path_obj.exists() and path_obj.is_file():
            raw_bytes = None
            # 1. Preferred: OpenCV
            if cv2 is not None:
                try:
                    img = cv2.imread(str(path_obj))
                    if img is not None:
                        resized = cv2.resize(img, target_resolution, interpolation=cv2.INTER_AREA)
                        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
                        success, buf = cv2.imencode(".jpg", resized, encode_param)
                        if success:
                            raw_bytes = buf.tobytes()
                except Exception as e:
                    print(f"[ESP32 LINK] OpenCV resize error: {e}")

            # 2. Fallback: PIL
            if raw_bytes is None and Image is not None:
                try:
                    with Image.open(path_obj) as im:
                        im_rgb = im.convert("RGB")
                        im_resized = im_rgb.resize(target_resolution, Image.Resampling.LANCZOS)
                        buf = io.BytesIO()
                        im_resized.save(buf, format="JPEG", quality=quality)
                        raw_bytes = buf.getvalue()
                except Exception as e:
                    print(f"[ESP32 LINK] PIL resize error: {e}")

            # 3. Fallback: raw file bytes
            if raw_bytes is None:
                with open(path_obj, "rb") as f:
                    raw_bytes = f.read()

            jpeg_len = len(raw_bytes)
            b64_data = base64.b64encode(raw_bytes).decode("ascii")
        else:
            print(f"[ESP32 LINK ERROR] Packet file not found: {packet_path}")

        # Hardware transmission mode
        if self.ser is not None and getattr(self.ser, "is_open", False):
            try:
                # 1. Send EVT metadata line
                evt_line = f"EVT:{event_type}|{score:.2f}|{justification}\n"
                self.ser.write(evt_line.encode("utf-8"))
                self.ser.flush()
                time.sleep(0.05)  # Short delay between lines for ESP32 UART buffer

                # 2. Send IMG base64 payload line
                if b64_data:
                    img_line = f"IMG:{b64_data}\n"
                    self.ser.write(img_line.encode("ascii"))
                    self.ser.flush()

                print(f"[ESP32 LINK TX] Sent EVT and IMG ({jpeg_len}B JPEG, {len(b64_data)} b64 chars) to ESP32 #1")
            except Exception as e:
                print(f"[ESP32 LINK ERROR] Serial write failed: {e}")
        else:
            # Simulated mode
            print(f"[SIMULATED TX] EVT:{event_type}|{score:.2f}|{justification} | IMG: {jpeg_len}B ({len(b64_data)} b64 chars)")

    def send_audit(self, event_type, action, justification):
        """Sends or logs an audit record explaining the decision."""
        iso_ts = datetime.now(timezone.utc).isoformat()
        if self.ser is None:
            print(f"[SIMULATED AUDIT] {iso_ts} | {event_type} | {action} | {justification}")
        else:
            print(f"[AUDIT LOG] {iso_ts} | {event_type} | {action} | {justification}")
