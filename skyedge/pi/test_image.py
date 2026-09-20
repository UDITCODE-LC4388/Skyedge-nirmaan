import argparse
import os
from pathlib import Path
import sys
import yaml
import cv2
from ultralytics import YOLO

# Ensure skyedge/pi is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)

from ai.detector import Detector
from priority.scorer import compute_priority
from resource.manager import ResourceManager
from transmission.compressor import compress


def test_images(image_path=None, image_dir=None, output_dir="custom_images/annotated", conf_thresh=0.25):
    config_path = BASE_DIR / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    model_path = cfg["ai"]["model_path"]
    event_classes = cfg.get("event_classes", list(cfg.get("mission_value", {}).keys()))
    weights = cfg["weights"]
    thresholds = cfg["thresholds"]
    mission_values = cfg["mission_value"]
    urgencies = cfg["urgency"]

    # Collect images
    images_to_test = []
    if image_path:
        p = Path(image_path)
        if not p.is_absolute():
            p = BASE_DIR / p
        if p.exists() and p.is_file():
            images_to_test.append(p)
        else:
            print(f"Error: File not found: {image_path}")
            return
    else:
        target_dir = Path(image_dir) if image_dir else BASE_DIR / "custom_images"
        if not target_dir.is_absolute():
            target_dir = BASE_DIR / target_dir

        if target_dir.exists():
            for ext in ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp"):
                images_to_test.extend(sorted(target_dir.glob(ext)))

    if not images_to_test:
        print(f"\n[!] No images found to test.")
        print(f"Please upload/copy your test images (.jpg, .png) to:")
        print(f"    {BASE_DIR / 'custom_images'}")
        print(f"\nThen run:")
        print(f"    python skyedge/pi/test_image.py")
        print(f"or:")
        print(f"    python skyedge/pi/test_image.py --image <path_to_image>")
        return

    out_dir_path = Path(output_dir)
    if not out_dir_path.is_absolute():
        out_dir_path = BASE_DIR / out_dir_path
    out_dir_path.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"SkyEdge Custom Image Test Suite")
    print(f"Model:          {model_path}")
    print(f"Images found:   {len(images_to_test)}")
    print(f"Annotated dir:  {out_dir_path}")
    print("=" * 70)

    # Initialize components
    detector = Detector(model_path=model_path, event_classes=event_classes)
    yolo_model = YOLO(model_path) if os.path.exists(model_path) else None
    resource_mgr = ResourceManager(cfg=cfg)

    routine_mv = mission_values.get("routine", 0.2)
    routine_urg = urgencies.get("routine", 0.1)
    aliases = {"fire": "wildfire", "smoke": "wildfire", "disaster_zone": "flood"}

    for idx, img_p in enumerate(images_to_test, 1):
        print(f"\n[{idx}/{len(images_to_test)}] Processing: {img_p.name}")

        # 1. Detector pipeline predict
        event_type, confidence, severity = detector.predict(str(img_p))

        # 2. Priority scoring
        lookup_key = event_type if event_type in mission_values else aliases.get(event_type, event_type)
        mission_val = mission_values.get(lookup_key, routine_mv)
        urgency_val = urgencies.get(lookup_key, routine_urg)

        score, base_action = compute_priority(
            severity=severity,
            confidence=confidence,
            urgency=urgency_val,
            mission_value=mission_val,
            weights=weights,
            thresholds=thresholds,
        )

        # 3. Resource decisions under different scenarios
        act_high_bw = resource_mgr.decide(score, base_action)

        resource_mgr.bandwidth = "CRITICAL"
        act_crit_bw = resource_mgr.decide(score, base_action)
        resource_mgr.bandwidth = "HIGH"  # reset

        # 4. Save visual annotated image with bounding boxes
        annotated_file = out_dir_path / f"annotated_{img_p.name}"
        if yolo_model:
            try:
                results = yolo_model.predict(str(img_p), imgsz=320, conf=conf_thresh, verbose=False)
                res_plotted = results[0].plot()

                # Add HUD overlay with SkyEdge score and action
                label_text = f"SkyEdge Score: {score} | Action: {base_action} | Event: {event_type}"
                cv2.putText(res_plotted, label_text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                cv2.imwrite(str(annotated_file), res_plotted)
            except Exception as e:
                print(f"Warning: Could not annotate image: {e}")
                annotated_file = None

        print(f"  • Detection:       {event_type.upper()} (conf={confidence:.2f}, severity={severity:.2f})")
        print(f"  • Priority Score:  {score:.2f} / 100")
        print(f"  • Base Action:     {base_action}")
        print(f"  • Normal Action:   {act_high_bw}")
        print(f"  • Critical Action: {act_crit_bw} (if satellite bandwidth is critical)")
        if annotated_file and annotated_file.exists():
            print(f"  • Annotated Image: {annotated_file}")

    print("\n" + "=" * 70)
    print("Testing complete. Annotated images saved to:")
    print(f"    {out_dir_path}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Test SkyEdge model & pipeline on custom images")
    parser.add_argument("--image", "-i", type=str, default=None, help="Path to a single custom image file")
    parser.add_argument("--dir", "-d", type=str, default=None, help="Directory of custom images (default: custom_images)")
    parser.add_argument("--output", "-o", type=str, default="custom_images/annotated", help="Directory for annotated images")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold (default: 0.25)")
    args = parser.parse_args()

    test_images(image_path=args.image, image_dir=args.dir, output_dir=args.output, conf_thresh=args.conf)


if __name__ == "__main__":
    main()
