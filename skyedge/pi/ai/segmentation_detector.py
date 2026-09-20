import os
from pathlib import Path
import numpy as np
from PIL import Image


def load_segmentation_config():
    try:
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
        if config_path.exists():
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                return cfg.get("segmentation", {})
    except Exception:
        pass
    return {}


class SegmentationDetector:
    """
    Edge segmentation detector using an ONNX-exported SegFormer model.
    Detects landslides by computing pixel coverage fraction and mean softmax confidence.
    """
    def __init__(self, model_path=None, image_size=None, noise_threshold=None, norm_mean=None, norm_std=None):
        seg_cfg = load_segmentation_config()
        self.model_path = model_path if model_path is not None else seg_cfg.get("model_path")
        self.image_size = image_size if image_size is not None else seg_cfg.get("image_size", 256)
        self.noise_threshold = noise_threshold if noise_threshold is not None else seg_cfg.get("noise_threshold", 0.02)
        norm_cfg = seg_cfg.get("normalization", {})
        self.norm_mean = norm_mean if norm_mean is not None else norm_cfg.get("mean", [0.485, 0.456, 0.406])
        self.norm_std = norm_std if norm_std is not None else norm_cfg.get("std", [0.229, 0.224, 0.225])
        self.session = None
        self.input_name = None

        if self.model_path:
            try:
                resolved_path = Path(self.model_path)
                if not resolved_path.is_absolute():
                    # Check relative to working directory or this file's parent
                    base_dir = Path(__file__).resolve().parent.parent
                    resolved_path = base_dir / self.model_path

                onnx_file = None
                if resolved_path.is_file() and resolved_path.suffix == ".onnx":
                    onnx_file = resolved_path
                elif resolved_path.is_dir() and (resolved_path / "model.onnx").exists():
                    onnx_file = resolved_path / "model.onnx"
                elif resolved_path.with_suffix(".onnx").exists():
                    onnx_file = resolved_path.with_suffix(".onnx")
                elif resolved_path.exists() and resolved_path.is_file():
                    onnx_file = resolved_path

                if not onnx_file or not onnx_file.exists():
                    raise FileNotFoundError(f"ONNX model file not found for path: '{self.model_path}'")

                import onnxruntime as ort
                # Load with default CPU/CoreML execution providers
                self.session = ort.InferenceSession(str(onnx_file))
                self.input_name = self.session.get_inputs()[0].name
            except Exception as e:
                print(f"[SEG_DETECTOR] Real model load failed: {e}")
                self.session = None
        else:
            self.session = None

    def predict(self, image_path):
        """
        Runs segmentation inference.
        Returns:
            - ("routine", 0.0, 0.0) if no landslide or session is None
            - ("landslide", confidence, severity) if landslide pixels exceed noise_threshold
        """
        if self.session is None:
            return "routine", 0.0, 0.0

        try:
            if not os.path.exists(image_path):
                return "routine", 0.0, 0.0

            # 1. Load and resize image
            img = Image.open(image_path).convert("RGB")
            img = img.resize((self.image_size, self.image_size), Image.Resampling.BILINEAR)

            # 2. Normalization from config: (x - mean) / std
            arr = np.array(img, dtype=np.float32) / 255.0
            mean = np.array(self.norm_mean, dtype=np.float32)
            std = np.array(self.norm_std, dtype=np.float32)
            norm_arr = (arr - mean) / std
            input_tensor = np.transpose(norm_arr, (2, 0, 1))[np.newaxis, ...].astype(np.float32)

            # 3. Run ONNX session
            logits = self.session.run(None, {self.input_name: input_tensor})[0]  # shape: (1, 2, H_out, W_out)

            # 4. Numerically stable softmax over class dimension (axis 1)
            e_x = np.exp(logits - np.max(logits, axis=1, keepdims=True))
            probs = e_x / np.sum(e_x, axis=1, keepdims=True)  # (1, 2, H_out, W_out)
            preds = np.argmax(logits[0], axis=0)              # (H_out, W_out)

            total_pixels = preds.size
            landslide_mask = (preds == 1)
            landslide_count = int(np.sum(landslide_mask))
            fraction = float(landslide_count / total_pixels)

            # 5. Check noise threshold
            if fraction < self.noise_threshold:
                return "routine", 0.0, 0.0

            # Confidence = mean probability of landslide class on predicted landslide pixels
            landslide_probs = probs[0, 1, :, :]
            mean_conf = float(np.mean(landslide_probs[landslide_mask]))

            confidence = round(min(max(mean_conf, 0.0), 1.0), 2)
            severity = round(min(max(fraction, 0.0), 1.0), 2)

            return "landslide", confidence, severity

        except Exception as e:
            print(f"[SEG_DETECTOR] Inference error: {e}")
            return "routine", 0.0, 0.0
