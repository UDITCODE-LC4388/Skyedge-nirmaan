import argparse
import os
from pathlib import Path
import shutil
import sys
import time
import yaml

# Add skyedge/pi to sys.path and set working directory to skyedge/pi
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)

from ai.detector import Detector
from ai.segmentation_detector import SegmentationDetector
from capture import capture
from display.oled_status import OLEDStatus
from priority.scorer import compute_priority
from priority.arbitration import arbitrate
from resource.manager import ResourceManager
from telemetry.arduino_link import ArduinoLink
from transmission.compressor import compress
from transmission.esp32_link import ESP32Link
from transmission.queue import PriorityQueue


def load_config(config_path="config.yaml"):
    path = Path(config_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"Config file not found at: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="SkyEdge Autonomous Pi Controller")
    parser.add_argument(
        "--cycles",
        type=int,
        default=None,
        help="Number of cycles to run (default: run indefinitely)",
    )
    parser.add_argument(
        "--image",
        type=str,
        default=None,
        help="Custom image path to process instead of camera capture",
    )
    args = parser.parse_args()

    # Allow setting cycle limit via CLI flag or CYCLES environment variable
    max_cycles = args.cycles
    if max_cycles is None and os.environ.get("CYCLES"):
        try:
            max_cycles = int(os.environ.get("CYCLES"))
        except ValueError:
            max_cycles = None

    # Load configuration
    cfg = load_config()

    # Extract configuration values - nothing hardcoded
    cycle_seconds = cfg.get("cycle_seconds", 3)
    weights = cfg["weights"]
    thresholds = cfg["thresholds"]
    mission_values = cfg["mission_value"]
    urgencies = cfg["urgency"]
    arduino_port = cfg["serial"]["arduino_port"]
    esp32_port = cfg["serial"]["esp32_port"]
    baud = cfg["serial"]["baud"]
    model_path = cfg["ai"]["model_path"]
    event_classes = cfg.get("event_classes", list(mission_values.keys()))
    tx_quality = cfg["compression"]["transmit_quality"]
    queued_quality = cfg["compression"]["queued_quality"]
    tx_dir = cfg["storage"]["transmitted"]
    queued_dir = cfg["storage"]["queued"]
    discarded_dir = cfg["storage"]["discarded"]

    # Construct all components
    detector = Detector(model_path=model_path, event_classes=event_classes)
    seg_cfg = cfg.get("segmentation", {})
    seg_model_path = seg_cfg.get("model_path")
    seg_image_size = seg_cfg.get("image_size")
    seg_noise_threshold = seg_cfg.get("noise_threshold")
    norm_cfg = seg_cfg.get("normalization", {})
    norm_mean = norm_cfg.get("mean")
    norm_std = norm_cfg.get("std")
    seg_detector = SegmentationDetector(
        model_path=seg_model_path,
        image_size=seg_image_size,
        noise_threshold=seg_noise_threshold,
        norm_mean=norm_mean,
        norm_std=norm_std,
    )
    arb_cfg = cfg.get("arbitration", {})
    tie_breaker_threshold = arb_cfg.get("tie_breaker_threshold", 0.0)
    arduino = ArduinoLink(port=arduino_port, baud=baud)
    esp32 = ESP32Link(port=esp32_port, baud=baud)
    resource_mgr = ResourceManager(cfg=cfg)
    queue = PriorityQueue()
    oled = OLEDStatus()

    print("=" * 60)
    print("SkyEdge Autonomous Pi Controller Initialized")
    print(f"Cycle interval: {cycle_seconds}s | Max cycles: {max_cycles if max_cycles is not None else 'infinite'}")
    print(f"Detector model: {model_path} | Event classes: {event_classes}")
    print(f"Segmentation model: {seg_model_path} | Noise threshold: {seg_noise_threshold}")
    print("=" * 60)

    cycle_count = 0
    try:
        while True:
            cycle_count += 1
            print(f"\n--- Cycle {cycle_count} ---")

            # 1. Read telemetry from Arduino
            telemetry = arduino.read()

            # 2. Update ResourceManager
            resource_mgr.update(telemetry)

            # 3. Capture image (real camera, fallback, or custom image if provided)
            image_path = args.image if args.image else capture(cfg)

            # 4. Run both Detector and SegmentationDetector predict() on the same captured image
            det_event, det_conf, det_sev = detector.predict(image_path)
            seg_event, seg_conf, seg_sev = seg_detector.predict(image_path)

            # Log both raw results before arbitration
            print(f"[DETECTION] YOLO: event={det_event} conf={det_conf} sev={det_sev}")
            print(f"[DETECTION] SegFormer: event={seg_event} conf={seg_conf} sev={seg_sev}")

            # 5. Look up mission_value / urgency and compute priority for both separately
            routine_mv = mission_values["routine"]
            routine_urg = urgencies["routine"]
            aliases = {"wildfire": "fire"}

            def score_event(ev_type, conf, sev):
                lookup_key = ev_type if ev_type in mission_values else aliases.get(ev_type, ev_type)
                mv = mission_values.get(lookup_key, routine_mv)
                urg = urgencies.get(lookup_key, routine_urg)
                return compute_priority(
                    severity=sev,
                    confidence=conf,
                    urgency=urg,
                    mission_value=mv,
                    weights=weights,
                    thresholds=thresholds,
                )

            det_score, det_action = score_event(det_event, det_conf, det_sev)
            seg_score, seg_action = score_event(seg_event, seg_conf, seg_sev)

            # 6. Arbitration: proceed with higher priority score (tie prefers original Detector via tie_breaker_threshold)
            winner, event_type, confidence, severity, priority_score, base_action = arbitrate(
                det_event=det_event,
                det_conf=det_conf,
                det_sev=det_sev,
                det_score=det_score,
                det_action=det_action,
                seg_event=seg_event,
                seg_conf=seg_conf,
                seg_sev=seg_sev,
                seg_score=seg_score,
                seg_action=seg_action,
                tie_breaker_threshold=tie_breaker_threshold,
            )
            other_score = det_score if winner == "SegFormer" else seg_score
            print(f"[ARBITRATION] {winner} won cycle (score={priority_score:.2f} vs {('YOLO' if winner == 'SegFormer' else 'SegFormer')}={other_score:.2f})")

            # 7. Get resource-adjusted action
            action = resource_mgr.decide(priority_score, base_action)

            # 8. Execute action
            if action == "TRANSMIT_NOW":
                compressed_path = compress(image_path, tx_quality, tx_dir)
                esp32.send(compressed_path, priority_score, event_type)
            elif action == "COMPRESS_AND_QUEUE":
                compressed_path = compress(image_path, queued_quality, queued_dir)
                queue.push(priority_score, (compressed_path, priority_score, event_type))
            elif action == "QUEUE_OR_STORE":
                os.makedirs(queued_dir, exist_ok=True)
                dest_file = os.path.join(queued_dir, Path(image_path).name)
                if os.path.exists(image_path) and os.path.abspath(image_path) != os.path.abspath(dest_file):
                    shutil.move(image_path, dest_file)
            elif action == "STORE_OR_DISCARD":
                os.makedirs(discarded_dir, exist_ok=True)
                dest_file = os.path.join(discarded_dir, Path(image_path).name)
                if os.path.exists(image_path) and os.path.abspath(image_path) != os.path.abspath(dest_file):
                    shutil.move(image_path, dest_file)
            else:
                # Fallback for any unrecognized action
                os.makedirs(discarded_dir, exist_ok=True)
                dest_file = os.path.join(discarded_dir, Path(image_path).name)
                if os.path.exists(image_path) and os.path.abspath(image_path) != os.path.abspath(dest_file):
                    shutil.move(image_path, dest_file)

            # 9. Drain the queue when bandwidth isn't CRITICAL
            if resource_mgr.bandwidth != "CRITICAL":
                while queue.peek() is not None:
                    queued_item = queue.pop()
                    if queued_item:
                        packet_path, q_score, q_event = queued_item
                        print(f"[QUEUE DRAIN] Transmitting queued item: {q_event} (score={q_score})")
                        esp32.send(packet_path, q_score, q_event)
                        # Move to transmitted
                        tx_dest = os.path.join(tx_dir, Path(packet_path).name)
                        if os.path.exists(packet_path) and os.path.abspath(packet_path) != os.path.abspath(tx_dest):
                            try:
                                shutil.move(packet_path, tx_dest)
                            except Exception:
                                pass

            # 10. Update OLED status display (and print summary line)
            oled.update(
                score=priority_score,
                action=action,
                telemetry=telemetry,
                bandwidth=resource_mgr.bandwidth,
                storage_pct=resource_mgr.storage_pct,
            )

            # Check termination condition for fixed-cycle test runs
            if max_cycles is not None and cycle_count >= max_cycles:
                print(f"\nReached target cycles ({max_cycles}). Terminating loop.")
                break

            time.sleep(cycle_seconds)

    except KeyboardInterrupt:
        print("\nSkyEdge Autonomous Controller stopped by user.")


if __name__ == "__main__":
    main()
