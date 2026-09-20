import os
import shutil
import sys
from pathlib import Path
import torch
import yaml
from ultralytics import YOLO

# Add skyedge/pi to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ai.prepare_merged_dataset import prepare_merged_dataset


def train():
    config_path = BASE_DIR / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found at: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 1. Read training parameters from config.yaml
    training_cfg = cfg.get("training", {})
    base_model_name = training_cfg.get("base_model", "yolov8n.pt")
    image_size = training_cfg.get("image_size", 320)
    epochs = training_cfg.get("epochs", 40)

    ai_cfg = cfg.get("ai", {})
    target_model_path = BASE_DIR / ai_cfg.get("model_path", "ai/models/best_ncnn_model")

    # 2. Ensure merged_data.yaml exists
    merged_yaml_path = BASE_DIR / "ai" / "datasets" / "merged_data.yaml"
    if not merged_yaml_path.exists():
        print("merged_data.yaml not found, generating...")
        merged_yaml_path = prepare_merged_dataset(BASE_DIR)

    project_dir = BASE_DIR / "ai" / "runs"
    project_dir.mkdir(parents=True, exist_ok=True)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print("=" * 60)
    print("Starting SkyEdge Edge AI Training")
    print(f"Base Model:  {base_model_name}")
    print(f"Image Size:  {image_size}")
    print(f"Epochs:      {epochs}")
    print(f"Device:      {device}")
    print(f"Data YAML:   {merged_yaml_path}")
    print(f"Save Dir:    {project_dir}")
    print(f"Deploy Dest: {target_model_path}")
    print("=" * 60)

    # 3. Run training
    model = YOLO(base_model_name)
    model.train(
        data=str(merged_yaml_path),
        epochs=epochs,
        imgsz=image_size,
        project=str(project_dir),
        name="merged_model",
        device=device,
        exist_ok=True,
        verbose=True,
    )

    print("\nTraining completed.")

    # 4. Locate best weights
    best_weights_path = project_dir / "merged_model" / "weights" / "best.pt"
    if not best_weights_path.exists():
        best_weights_path = project_dir / "merged_model" / "weights" / "last.pt"

    print(f"\nLoading best trained weights from: {best_weights_path}")
    best_model = YOLO(str(best_weights_path))

    # 5. Validate best model
    val_results = best_model.val(data=str(merged_yaml_path), imgsz=image_size, verbose=True)
    print("\n--- Final Validation Metrics ---")
    if hasattr(val_results, "box"):
        print(f"mAP50-95: {val_results.box.map:.4f}")
        print(f"mAP50:    {val_results.box.map50:.4f}")
        print(f"mAP75:    {val_results.box.map75:.4f}")

    # 6. Export to NCNN
    print(f"\nExporting {best_weights_path} to NCNN format (imgsz={image_size})...")
    exported_path = best_model.export(format="ncnn", imgsz=image_size)
    print(f"NCNN export output: {exported_path}")

    # 7. Copy exported model folder to target_model_path in config.yaml
    target_model_path.parent.mkdir(parents=True, exist_ok=True)
    if target_model_path.exists():
        if target_model_path.is_dir():
            shutil.rmtree(target_model_path)
        else:
            target_model_path.unlink()

    if os.path.isdir(exported_path):
        shutil.copytree(exported_path, target_model_path)
    else:
        shutil.copy2(exported_path, target_model_path)

    print(f"\n✅ NCNN model successfully deployed to: {target_model_path}")
    print(f"Model contents in {target_model_path}:")
    for item in target_model_path.iterdir():
        print(f"  - {item.name}")

    return target_model_path


if __name__ == "__main__":
    train()
