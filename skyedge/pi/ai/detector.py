import os
import random


class Detector:
    def __init__(self, model_path, event_classes):
        self.model_path = model_path
        self.event_classes = list(event_classes)
        self.model = None

        if model_path:
            try:
                if not os.path.exists(model_path):
                    raise FileNotFoundError(f"Model path does not exist: '{model_path}'")
                from ultralytics import YOLO
                self.model = YOLO(model_path)
            except Exception as e:
                print(f"[DETECTOR] Real model load failed: {e}")
                self.model = None
        else:
            self.model = None

    def predict(self, image_path):
        if self.model is not None:
            try:
                results = self.model.predict(image_path, imgsz=320, verbose=False)
                if results and len(results) > 0 and len(results[0].boxes) > 0:
                    top_box = results[0].boxes[0]
                    cls_id = int(top_box.cls[0].item())
                    names = results[0].names
                    event_type = names.get(cls_id, str(cls_id)) if names else str(cls_id)
                    confidence = float(top_box.conf[0].item())

                    orig_shape = results[0].orig_shape
                    if orig_shape and orig_shape[0] > 0 and orig_shape[1] > 0:
                        xyxy = top_box.xyxy[0].tolist()
                        box_area = (xyxy[2] - xyxy[0]) * (xyxy[3] - xyxy[1])
                        img_area = orig_shape[0] * orig_shape[1]
                        norm_area = min(max(box_area / img_area, 0.0), 1.0)
                        severity = round(min(max(norm_area * 0.7 + confidence * 0.3, 0.0), 1.0), 2)
                    else:
                        severity = round(confidence, 2)

                    return event_type, round(confidence, 2), severity
            except Exception as e:
                print(f"[DETECTOR] Inference error: {e}")

            # Model loaded but 0 boxes detected -> routine scene
            return "routine", 0.0, 0.0

        # Simulated fallback mode (only runs when self.model is None)
        event_type = random.choice(self.event_classes)
        confidence = round(random.uniform(0.5, 0.99), 2)
        severity = round(random.uniform(0.3, 0.95), 2)
        print(f"[SIMULATED DETECTION] event={event_type} confidence={confidence} severity={severity} image={image_path}")
        return event_type, confidence, severity
