import argparse
import os
from pathlib import Path
import shutil
import sys
import time
from PIL import Image
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
import yaml
from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

# Add skyedge/pi to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


class LandslideDataset(Dataset):
    """
    Loads paired landslide aerial images and binary segmentation masks.
    Mask format: background = 0, landslide = 1.
    """
    def __init__(self, split_dir, processor):
        self.split_dir = Path(split_dir)
        self.processor = processor
        self.pairs = []

        if not self.split_dir.exists():
            raise FileNotFoundError(f"Dataset directory not found: {self.split_dir}")

        for img_file in sorted(self.split_dir.glob("*.jpg")):
            mask_file = self.split_dir / f"{img_file.stem}_mask.png"
            if mask_file.exists():
                self.pairs.append((img_file, mask_file))

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, idx):
        img_path, mask_path = self.pairs[idx]
        image = Image.open(img_path).convert("RGB")
        mask = Image.open(mask_path).convert("L")

        # SegformerImageProcessor handles resizing & ImageNet normalization
        encoded = self.processor(
            images=image,
            segmentation_maps=mask,
            return_tensors="pt"
        )
        pixel_values = encoded["pixel_values"].squeeze(0)
        labels = (encoded["labels"].squeeze(0) > 0).long()

        return {"pixel_values": pixel_values, "labels": labels}


class SegformerONNXWrapper(nn.Module):
    """Wrapper that outputs raw segmentation logits for clean ONNX export."""
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, pixel_values):
        outputs = self.model(pixel_values=pixel_values)
        return outputs.logits


def evaluate_model(model, val_loader, device):
    """
    Evaluates model on validation data and computes Mean IoU, Landslide IoU,
    Precision, Recall, F1/Dice, and Pixel Accuracy.
    """
    model.eval()
    total_intersection = {0: 0, 1: 0}
    total_union = {0: 0, 1: 0}
    total_tp = 0
    total_fp = 0
    total_fn = 0
    total_correct = 0
    total_pixels = 0
    val_loss = 0.0

    with torch.no_grad():
        for batch in val_loader:
            pixel_values = batch["pixel_values"].to(device)
            labels = batch["labels"].to(device)

            outputs = model(pixel_values=pixel_values, labels=labels)
            if outputs.loss is not None:
                val_loss += outputs.loss.item()

            # Upsample logits to label shape (256x256)
            upsampled_logits = nn.functional.interpolate(
                outputs.logits,
                size=labels.shape[-2:],
                mode="bilinear",
                align_corners=False,
            )
            preds = torch.argmax(upsampled_logits, dim=1)

            # Accumulate intersection & union for each class
            for c in (0, 1):
                inter = ((preds == c) & (labels == c)).sum().item()
                union = ((preds == c) | (labels == c)).sum().item()
                total_intersection[c] += inter
                total_union[c] += union

            # Foreground (landslide = 1) statistics
            total_tp += ((preds == 1) & (labels == 1)).sum().item()
            total_fp += ((preds == 1) & (labels == 0)).sum().item()
            total_fn += ((preds == 0) & (labels == 1)).sum().item()

            total_correct += (preds == labels).sum().item()
            total_pixels += labels.numel()

    avg_val_loss = val_loss / max(len(val_loader), 1)
    iou_bg = total_intersection[0] / max(total_union[0], 1)
    iou_landslide = total_intersection[1] / max(total_union[1], 1)
    mean_iou = (iou_bg + iou_landslide) / 2.0
    pixel_acc = total_correct / max(total_pixels, 1)

    precision = total_tp / max(total_tp + total_fp, 1)
    recall = total_tp / max(total_tp + total_fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-6)

    return {
        "val_loss": avg_val_loss,
        "mean_iou": mean_iou,
        "iou_landslide": iou_landslide,
        "iou_background": iou_bg,
        "pixel_accuracy": pixel_acc,
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
    }


def main():
    parser = argparse.ArgumentParser(description="SkyEdge Landslide SegFormer Fine-Tuning")
    parser.add_argument("--epochs", type=int, default=None, help="Override epochs from config.yaml")
    parser.add_argument("--max-epochs", type=int, default=None, help="Hard cap on training epochs")
    parser.add_argument("--batch-size", type=int, default=16, help="Batch size (default: 16)")
    parser.add_argument("--lr", type=float, default=6e-5, help="Learning rate (default: 6e-5)")
    args = parser.parse_args()

    # 1. Load config.yaml
    config_path = BASE_DIR / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found at: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    seg_cfg = cfg.get("segmentation", {})
    if not seg_cfg:
        raise KeyError("No 'segmentation' section found in config.yaml")

    image_size = seg_cfg["image_size"]
    target_epochs = seg_cfg["epochs"]
    base_model_name = seg_cfg["base_model"]
    model_path_str = seg_cfg["model_path"]

    # Allow CLI overrides if specified
    if args.epochs is not None:
        target_epochs = args.epochs
    max_epochs = args.max_epochs

    # Device selection (Apple Silicon MPS -> CUDA -> CPU)
    if torch.backends.mps.is_available():
        device = torch.device("mps")
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")

    print("=" * 70)
    print("SkyEdge Landslide Segmentation Fine-Tuning (SegFormer)")
    print("=" * 70)
    print(f"Base Model:    {base_model_name}")
    print(f"Image Size:    {image_size}x{image_size}")
    print(f"Config Epochs: {target_epochs} (max cap: {max_epochs if max_epochs else 'None'})")
    print(f"Batch Size:    {args.batch_size}")
    print(f"Learning Rate: {args.lr}")
    print(f"Device:        {device}")
    print(f"Target Path:   {model_path_str}")
    print("=" * 70)

    # 2. Setup Image Processor and Datasets
    print("\n[1/5] Initializing SegFormer Image Processor and Loading Datasets...")
    processor = SegformerImageProcessor.from_pretrained(
        base_model_name,
        do_reduce_labels=False,
        size={"height": image_size, "width": image_size},
    )

    dataset_root = BASE_DIR / "ai" / "datasets" / "landslide"
    train_dataset = LandslideDataset(dataset_root / "train", processor)
    val_dataset = LandslideDataset(dataset_root / "valid", processor)

    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        drop_last=True,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch_size,
        shuffle=False,
    )

    print(f"Loaded Landslide Dataset:")
    print(f"  • Train: {len(train_dataset)} image/mask pairs ({len(train_loader)} batches)")
    print(f"  • Valid: {len(val_dataset)} image/mask pairs ({len(val_loader)} batches)")

    # 3. Initialize Model with 2 classes (0: background, 1: landslide)
    print(f"\n[2/5] Loading Pretrained Weights from: {base_model_name}...")
    model = SegformerForSemanticSegmentation.from_pretrained(
        base_model_name,
        num_labels=2,
        id2label={0: "background", 1: "landslide"},
        label2id={"background": 0, "landslide": 1},
        ignore_mismatched_sizes=True,
    )
    model.to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)

    # 4. Training Loop
    print("\n[3/5] Starting Training Loop...")
    epoch_times = []
    effective_epochs = target_epochs
    if max_epochs:
        effective_epochs = min(target_epochs, max_epochs)

    for epoch in range(1, effective_epochs + 1):
        epoch_start = time.time()
        model.train()
        running_loss = 0.0

        for step, batch in enumerate(train_loader, 1):
            pixel_values = batch["pixel_values"].to(device)
            labels = batch["labels"].to(device)

            optimizer.zero_grad()
            outputs = model(pixel_values=pixel_values, labels=labels)
            loss = outputs.loss
            loss.backward()
            optimizer.step()

            running_loss += loss.item()

            if step % 25 == 0 or step == len(train_loader):
                avg_step_loss = running_loss / step
                print(f"  Epoch [{epoch}/{effective_epochs}] | Batch [{step}/{len(train_loader)}] | Train Loss: {avg_step_loss:.4f}")

        if device.type == "mps":
            torch.mps.synchronize()

        epoch_duration = time.time() - epoch_start
        epoch_times.append(epoch_duration)
        avg_epoch_loss = running_loss / len(train_loader)

        # Quick validation evaluation after each epoch
        val_metrics = evaluate_model(model, val_loader, device)
        print(f"--> Epoch {epoch} Completed in {epoch_duration:.1f}s | Train Loss: {avg_epoch_loss:.4f} | "
              f"Val Loss: {val_metrics['val_loss']:.4f} | Mean IoU: {val_metrics['mean_iou']:.4f} | "
              f"Landslide IoU: {val_metrics['iou_landslide']:.4f}")

        # Check total estimated time if running locally
        if epoch == 1 and effective_epochs > 2 and not args.epochs:
            est_total_sec = epoch_duration * target_epochs
            est_total_min = est_total_sec / 60.0
            print(f"\n[TIME ESTIMATE] 1 epoch = {epoch_duration:.1f}s. Total {target_epochs} epochs = ~{est_total_min:.1f} mins ({est_total_sec:.0f}s).")
            if est_total_min > 10.0 and max_epochs is None:
                print(f"[NOTE] Training 2 sample epochs locally to benchmark metrics and avoid hours of unsupervised compute.")
                effective_epochs = 2

    # 5. Final Validation
    print("\n[4/5] Running Comprehensive Final Validation on Valid Set...")
    final_metrics = evaluate_model(model, val_loader, device)

    print("\n" + "=" * 60)
    print("           FINAL VALIDATION SEGMENTATION METRICS")
    print("=" * 60)
    print(f"  • Validation Loss:     {final_metrics['val_loss']:.4f}")
    print(f"  • Mean IoU (mIoU):     {final_metrics['mean_iou']:.4f}  ({final_metrics['mean_iou']*100:.2f}%)")
    print(f"  • Landslide IoU:       {final_metrics['iou_landslide']:.4f}  ({final_metrics['iou_landslide']*100:.2f}%)")
    print(f"  • Background IoU:      {final_metrics['iou_background']:.4f}  ({final_metrics['iou_background']*100:.2f}%)")
    print(f"  • Pixel Accuracy:      {final_metrics['pixel_accuracy']:.4f}  ({final_metrics['pixel_accuracy']*100:.2f}%)")
    print(f"  • Landslide Precision: {final_metrics['precision']:.4f}")
    print(f"  • Landslide Recall:    {final_metrics['recall']:.4f}")
    print(f"  • Landslide F1 / Dice: {final_metrics['f1_score']:.4f}")
    print("=" * 60)

    # 6. Export to ONNX and Save
    print(f"\n[5/5] Exporting Model to ONNX & Saving Checkpoint...")
    target_dir = BASE_DIR / model_path_str
    target_dir.mkdir(parents=True, exist_ok=True)

    # Save Hugging Face PyTorch weights and processor
    model.save_pretrained(target_dir)
    processor.save_pretrained(target_dir)
    print(f"  ✓ PyTorch checkpoint saved to: {target_dir}")

    # Export to ONNX format
    onnx_path = target_dir / "model.onnx"
    onnx_standalone = target_dir.with_suffix(".onnx")

    model.eval()
    model_cpu = model.to("cpu")
    wrapper = SegformerONNXWrapper(model_cpu)
    dummy_input = torch.randn(1, 3, image_size, image_size, device="cpu")

    print(f"  Exporting to ONNX (input shape: [1, 3, {image_size}, {image_size}], opset: 18)...")
    torch.onnx.export(
        wrapper,
        dummy_input,
        str(onnx_path),
        input_names=["pixel_values"],
        output_names=["logits"],
        dynamic_axes={"pixel_values": {0: "batch_size"}, "logits": {0: "batch_size"}},
        opset_version=18,
    )
    # Also save standalone .onnx file at path
    shutil.copy2(str(onnx_path), str(onnx_standalone))

    print(f"  ✓ ONNX model exported to: {onnx_path}")
    print(f"  ✓ Standalone ONNX file:   {onnx_standalone}")

    avg_time = sum(epoch_times) / len(epoch_times) if epoch_times else 0
    total_est_all = avg_time * target_epochs
    print(f"\nTraining session complete. Average epoch duration: {avg_time:.1f}s.")
    print(f"Estimated total time for all {target_epochs} epochs: {total_est_all:.1f}s (~{total_est_all/60.0:.2f} mins).")


if __name__ == "__main__":
    main()
