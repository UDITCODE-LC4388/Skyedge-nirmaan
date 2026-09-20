# SkyEdge Autonomous Mission System

Autonomous Edge Intelligence & Remote Incident Response System.

SkyEdge combines onboard dual-model Edge AI (YOLOv8 Object Detection + SegFormer Semantic Segmentation), priority-based incident arbitration, dynamic resource management, and wireless ESP-NOW telemetry downlinks.

---

## System Architecture

```
[Sensors]
  DHT11 (Pin 2) ---------+
  LDR DO (Pin 3) --------+
  ACS712 (Pin A1) -------+---> [Arduino Uno] (Telemetry Firmware)
  Volt Sensor (Pin A2) --+        | USB Serial (/dev/ttyACM0 @ 115200)
  MPU6050 (A4/A5 I2C) ---+        v
                            [Raspberry Pi] (Autonomous Mission Controller)
                              | UART2 (TX GPIO 14 -> RX GPIO 16)
                              |       (RX GPIO 15 <- TX GPIO 17)
                              v
                            [ESP32 Downlink] (Airborne Transmitter)
                              |
                              | )))) 2.4 GHz ESP-NOW Broadcast ))))
                              v
                            [ESP32 Ground] (Ground Station Receiver)
```

---

## Directory Structure

* **`skyedge/pi/`**: Python mission controller on Raspberry Pi:
  * `main.py`: Main autonomous execution loop (Capture $\rightarrow$ Dual AI $\rightarrow$ Arbitrate $\rightarrow$ Decide $\rightarrow$ Act $\rightarrow$ Transmit).
  * `ai/models/best_ncnn_model/`: 4-class YOLOv8 NCNN edge model (`fire`, `smoke`, `disaster_zone`, `flood`).
  * `ai/models/landslide_segformer/`: Fine-tuned SegFormer ONNX landslide segmentation model.
  * `priority/`: Priority scoring and model arbitration logic.
  * `resource/`: Dynamic bandwidth and storage constraints manager.
  * `telemetry/`: Arduino serial communication and simulated fallback.
  * `transmission/`: Priority queue, JPEG compressor, and ESP32 UART link.
  * `display/`: I2C SSD1306 OLED status display.
  * `deploy/`: Systemd service unit (`skyedge.service`) and deployment checklist (`DEPLOY_STEPS.md`).
  * `tests/`: Automated pytest unit test suite (21/21 passing).
* **`skyedge/firmware/`**: Embedded C/C++ firmware sketches:
  * `arduino_uno/skyedge_telemetry.ino`: Sensor telemetry collection over I2C & analog pins, JSON serialization.
  * `esp32_downlink/skyedge_downlink.ino`: Airborne transceiver (UART2 from Pi $\rightarrow$ ESP-NOW broadcast).
  * `esp32_ground/skyedge_ground.ino`: Ground station receiver with packet display & RSSI reporting.
* **`skyedge/BENCH_TEST_CHECKLIST.md`**: Step 14 full-chain hardware bench test checklist.

---

## Quick Start (Raspberry Pi)

Follow the detailed guide in [`skyedge/pi/deploy/DEPLOY_STEPS.md`](skyedge/pi/deploy/DEPLOY_STEPS.md):

```bash
cd /home/pi
git clone https://github.com/UDITCODE-LC4388/Skyedge-nirmaan.git skyedge
cd skyedge/skyedge/pi
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
python3 main.py
```
