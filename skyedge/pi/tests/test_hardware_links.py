import sys
from pathlib import Path
import pytest
import yaml

# Ensure skyedge/pi is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from telemetry.arduino_link import ArduinoLink
from transmission.esp32_link import ESP32Link


@pytest.fixture(scope="module")
def cfg():
    config_path = BASE_DIR / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_arduino_link_simulation_fallback(cfg):
    # 1. Instantiate ArduinoLink with a port name that will not exist (from config.yaml)
    port = cfg["serial"]["arduino_port"]
    baud = cfg["serial"]["baud"]
    arduino = ArduinoLink(port=port, baud=baud)

    # Confirms it falls back to simulated mode without crashing
    assert arduino.ser is None

    # Confirms read() returns a dict containing all 9 hardware telemetry keys
    telemetry = arduino.read()
    assert isinstance(telemetry, dict)
    expected_keys = [
        "voltage", "current", "temperature", "illumination",
        "accel_x", "accel_y", "accel_z", "storage_pct", "bandwidth"
    ]
    for key in expected_keys:
        assert key in telemetry, f"Missing telemetry key: {key}"

    assert 4.6 <= telemetry["voltage"] <= 5.1
    assert 0.1 <= telemetry["current"] <= 0.3
    assert 20.0 <= telemetry["temperature"] <= 35.0
    assert telemetry["illumination"] in [0, 1]
    assert -1.0 <= telemetry["accel_x"] <= 1.0
    assert -1.0 <= telemetry["accel_y"] <= 1.0
    assert 9.5 <= telemetry["accel_z"] <= 10.0
    assert 10 <= telemetry["storage_pct"] <= 90
    assert telemetry["bandwidth"] in ["HIGH", "MEDIUM", "LOW", "CRITICAL"]


def test_esp32_link_simulation_fallback(cfg, capsys):
    # 2. Instantiate ESP32Link with non-existent port (from config.yaml)
    port = cfg["serial"]["esp32_port"]
    baud = cfg["serial"]["baud"]
    esp = ESP32Link(port=port, baud=baud)

    # Confirms it falls back to simulated mode without crashing
    assert esp.ser is None

    # Confirm send() runs without error and prints simulated line when no hardware is present
    esp.send(packet_path="storage/queued/test.jpg", score=92.5, event_type="wildfire")
    captured = capsys.readouterr()
    assert "[SIMULATED TX] wildfire score=92.5 packet=storage/queued/test.jpg" in captured.out
