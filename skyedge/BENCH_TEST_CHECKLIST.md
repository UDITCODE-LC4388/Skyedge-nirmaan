# SkyEdge Step 14: Full-Chain Bench Test Checklist

This document details the complete end-to-end hardware verification procedure for the SkyEdge Autonomous Mission System prior to final enclosure assembly and deployment.

---

## 1. Hardware Interconnect Diagram

Before applying power, verify the physical wiring connections:

```
[Sensors]
  DHT11 (Pin 2) ---------+
  LDR DO (Pin 3) --------+
  ACS712 (Pin A1) -------+---> [Arduino Uno] (Telemetry)
  Volt Sensor (Pin A2) --+        | USB (Serial /dev/ttyACM0 @ 115200)
  MPU6050 (A4/A5 I2C) ---+        v
                            [Raspberry Pi] (Mission Controller)
                              | UART2 (TX GPIO 14 -> ESP32 RX2 GPIO 16)
                              |       (RX GPIO 15 <- ESP32 TX2 GPIO 17)
                              | Common GND
                              v
                            [ESP32 Downlink] (Airborne Transmitter)
                              |
                              | )))) 2.4 GHz ESP-NOW Broadcast ))))
                              v
                            [ESP32 Ground] (Ground Receiver Node)
                              | USB Serial @ 115200
                              v
                            [Laptop / Ground Terminal]
```

---

## 2. Step 14 Full-Chain Bench Test Procedure

Follow these steps in strict numbered order:

### Step 1: Hardware Power-Up Sequence
1. Connect ground station ESP32 to laptop USB.
2. Connect airborne ESP32 downlink board to Pi (TX/RX/GND) and power.
3. Connect Arduino Uno via USB cable to Raspberry Pi.
4. Power up Raspberry Pi via dedicated 5V 3A supply.
5. Verify power LEDs on all 4 boards: Arduino Uno, Raspberry Pi, ESP32 Downlink, and ESP32 Ground.

---

### Step 2: Verify Arduino Telemetry Output (Isolated Check)
1. If testing Arduino separately via laptop serial monitor, open port at **115200 baud**.
2. Confirm a valid JSON telemetry line is printed once per second:
   ```json
   {"voltage":12.14,"current":0.22,"temperature":25.4,"illumination":1,"accel_x":0.02,"accel_y":-0.01,"accel_z":9.81,"storage_pct":0,"bandwidth":"HIGH"}
   ```
3. Verify sensor responsiveness:
   - Cover/illuminate the LDR: confirm `"illumination"` flips between `0` and `1`.
   - Tilt the MPU6050: confirm `"accel_x"`, `"accel_y"`, `"accel_z"` values change.
   - Warm DHT11 with fingers: confirm `"temperature"` increases.

---

### Step 3: Verify Raspberry Pi Autonomous Controller & OLED
1. Start the controller (or verify systemd service is active):
   ```bash
   cd /home/pi/skyedge/pi
   source venv/bin/activate
   python3 main.py
   ```
2. Verify console cycling every 3 seconds:
   - Camera captures frame to `storage/incoming/frame.jpg` (or falls back to test image if camera disconnected).
   - Dual edge models execute:
     - YOLOv8 NCNN runs on image (`fire`, `smoke`, `disaster_zone`, `flood`).
     - SegFormer ONNX runs on image (`landslide`).
   - Priority arbitration calculates score based on mission weights and urgency.
   - OLED screen updates with current Score, Action, Bandwidth, and Voltage.

---

### Step 4: Verify ESP32 Downlink Transmission (Airborne Node)
1. Open USB Serial Monitor on the airborne ESP32 board (`115200 baud`).
2. If `TEST_MODE` was active for bench testing, confirm you set:
   ```cpp
   #define TEST_MODE false
   ```
   so packets are driven by real UART2 incoming lines from the Raspberry Pi.
3. When the Pi triggers an incident with score $\ge 90$ (`TRANSMIT_NOW`), confirm ESP32 prints:
   ```text
   TX (ESP-NOW): {"packet_path":"storage/transmitted/...","score":93.4,"event_type":"flood"}
   ```

---

### Step 5: Verify ESP32 Ground Station Reception
1. Open USB Serial Monitor on the ground station ESP32 (`115200 baud`).
2. Confirm incoming broadcast packets appear within milliseconds of transmission:
   ```text
   RX (ESP-NOW): {"packet_path":"storage/transmitted/...","score":93.4,"event_type":"flood"} RSSI:-45
   ```
3. Verify RSSI signal strength changes as distance between the two ESP32 units varies.

---

### Step 6: Resource Manager Bandwidth-Flip Verification
Test that low-bandwidth conditions hold back routine images while allowing critical disasters to transmit immediately:

1. **Simulate CRITICAL Bandwidth**:
   - In `skyedge/pi/config.yaml`, temporarily set or simulate via telemetry:
     ```yaml
     # Bandwidth critical threshold is 5
     ```
   - Or simulate by adjusting incoming telemetry `bandwidth: "CRITICAL"`.
2. **Routine / Moderate Incident Test (Score < 90)**:
   - Feed a non-critical event (e.g. routine scene or low-confidence anomaly, score ~45).
   - Expected Result: Resource Manager overrides base action to `QUEUE_OR_STORE`.
   - Verify image is compressed and stored in `storage/queued/` (held back from transmission).
3. **Critical High-Priority Incident Test (Score $\ge$ 90)**:
   - Feed a critical event (e.g. high-confidence `wildfire` or `flood` test image, score $\ge 90$).
   - Expected Result: Resource Manager allows the critical incident through despite `CRITICAL` bandwidth (`TRANSMIT_NOW`).
   - Verify image moves to `storage/transmitted/`, Pi sends JSON over UART2, and ESP32 Ground prints the received packet immediately.
4. Restore standard config settings after test completion.

---

## 3. Pre-Build Freeze Checklist (Sept 25–26 Build Days)

Complete and freeze each of the following components before field assembly:

### A. Edge AI Models & Weights
- [x] **YOLOv8 NCNN 4-Class Detector**: Deployed at `ai/models/best_ncnn_model/` (`fire`, `smoke`, `disaster_zone`, `flood`).
  - **Overall**: mAP50 = 59.17% (0.592), mAP50-95 = 32.79% (0.328), Precision = 68.4%, Recall = 53.6%
  - **fire**: P = 65.1%, R = 48.0%, mAP50 = 54.5% (817 instances)
  - **smoke**: P = 63.0%, R = 40.7%, mAP50 = 46.4% (182 instances)
  - **disaster_zone**: P = 76.5%, R = 85.4%, mAP50 = 85.7% (103 instances)
  - **flood**: P = 68.8%, R = 40.5%, mAP50 = 50.1% (529 instances)
- [x] **SegFormer ONNX Landslide Model**: Deployed at `ai/models/landslide_segformer/model.onnx`. Fine-tuned on landslide semantic masks.
- [x] **Config Weights & Thresholds**: Frozen in `config.yaml`:
  - `severity: 0.35`, `confidence: 0.20`, `urgency: 0.25`, `mission_value: 0.20`
  - `transmit_now: 90`, `compress_and_queue: 70`, `queue_or_store: 40`

### B. Firmware & Pin Mappings
- [x] **Arduino Uno Telemetry (`skyedge_telemetry.ino`)**:
  - DHT11 $\rightarrow$ Pin 2
  - LDR DO (Digital) $\rightarrow$ Pin 3
  - ACS712 Current $\rightarrow$ Pin A1
  - Voltage Divider Module $\rightarrow$ Pin A2
  - MPU6050 IMU $\rightarrow$ Pin A4 (SDA), Pin A5 (SCL), AD0 to GND (Address `0x68`)
- [x] **ESP32 Downlink (`skyedge_downlink.ino`)**:
  - UART2 $\rightarrow$ RX GPIO 16, TX GPIO 17
  - ESP-NOW Broadcast $\rightarrow$ `FF:FF:FF:FF:FF:FF`
  - Set `#define TEST_MODE false` for flight operation
- [x] **ESP32 Ground Station (`skyedge_ground.ino`)**:
  - ESP-NOW receive handler with RSSI logging

### C. Raspberry Pi Software Stack
- [x] **Software Dependencies**: Documented in `skyedge/pi/requirements.txt`.
- [x] **Systemd Auto-Start Service**: Configured in `skyedge/pi/deploy/skyedge.service`.
- [x] **Deployment Steps**: Documented in `skyedge/pi/deploy/DEPLOY_STEPS.md`.
- [x] **Automated Test Suite**: 14/14 tests passing (`pytest tests/ -v`).

### D. Physical Hardware & Power Preparedness
- [ ] 64-bit Raspberry Pi OS Lite MicroSD card flashed and tested.
- [ ] Logic level matching: confirm 3.3V UART compatibility between Pi GPIO 14/15 and ESP32 GPIO 16/17 (both are native 3.3V LVTTL).
- [ ] Shared common ground rail connected between Arduino, Raspberry Pi, and ESP32.
- [ ] MicroSD card storage formatted and verified on Raspberry Pi.
- [ ] Spare jumper wires, USB-C power cables, and 10kΩ resistors packed for build days.
