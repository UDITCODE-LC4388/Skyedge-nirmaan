import os
import sys
from pathlib import Path
import yaml

TARGET_CLASSES = ["fire", "smoke", "disaster_zone", "flood"]


def normalize_name(name):
    return str(name).strip().lower().replace(" ", "_")


def format_box_line(cls_id, coords):
    """
    Given class_id and coords (either standard 4 bbox values or 2N polygon coords),
    returns YOLO formatted line: '<cls_id> <x_center> <y_center> <width> <height>\n'.
    """
    try:
        vals = [float(x) for x in coords]
    except ValueError:
        return None

    if len(vals) == 4:
        return f"{cls_id} {vals[0]:.6f} {vals[1]:.6f} {vals[2]:.6f} {vals[3]:.6f}\n"
    elif len(vals) >= 6 and len(vals) % 2 == 0:
        xs = vals[0::2]
        ys = vals[1::2]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        w = max(0.0, min(1.0, max_x - min_x))
        h = max(0.0, min(1.0, max_y - min_y))
        xc = max(0.0, min(1.0, min_x + w / 2.0))
        yc = max(0.0, min(1.0, min_y + h / 2.0))
        return f"{cls_id} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n"
    return None


def prepare_merged_dataset(base_dir: Path):
    wildfire_yaml_path = base_dir / "data.yaml"
    earthquake_yaml_path = base_dir / "ai" / "datasets" / "earthquake" / "data.yaml"
    flood_yaml_path = base_dir / "ai" / "datasets" / "flood" / "data.yaml"

    # Verify original data.yaml files exist
    if not wildfire_yaml_path.exists():
        raise FileNotFoundError(f"Wildfire data.yaml is missing at: {wildfire_yaml_path}")
    if not earthquake_yaml_path.exists():
        raise FileNotFoundError(f"Earthquake data.yaml is missing at: {earthquake_yaml_path}")
    if not flood_yaml_path.exists():
        raise FileNotFoundError(f"Flood data.yaml is missing at: {flood_yaml_path}")

    # Read original data.yaml files
    with open(wildfire_yaml_path, "r", encoding="utf-8") as f:
        wf_cfg = yaml.safe_load(f)
    with open(earthquake_yaml_path, "r", encoding="utf-8") as f:
        eq_cfg = yaml.safe_load(f)
    with open(flood_yaml_path, "r", encoding="utf-8") as f:
        fl_cfg = yaml.safe_load(f)

    wf_names = [normalize_name(x) for x in wf_cfg.get("names", [])]
    eq_names = [normalize_name(x) for x in eq_cfg.get("names", [])]
    fl_names = [normalize_name(x) for x in fl_cfg.get("names", [])]

    print(f"Wildfire original classes:   {wf_cfg.get('names', [])}")
    print(f"Earthquake original classes: {eq_cfg.get('names', [])}")
    print(f"Flood original classes:      {fl_cfg.get('names', [])}")

    # Derive class remapping dynamically
    wf_mapping = {}
    for target_idx, target_name in enumerate(TARGET_CLASSES):
        if target_name in wf_names:
            orig_idx = wf_names.index(target_name)
            wf_mapping[str(orig_idx)] = target_idx

    eq_mapping = {}
    for target_idx, target_name in enumerate(TARGET_CLASSES):
        if target_name in eq_names:
            orig_idx = eq_names.index(target_name)
            eq_mapping[str(orig_idx)] = target_idx

    fl_mapping = {}
    for target_idx, target_name in enumerate(TARGET_CLASSES):
        if target_name in fl_names:
            orig_idx = fl_names.index(target_name)
            fl_mapping[str(orig_idx)] = target_idx

    print(f"Derived Wildfire   -> Merged mapping: {wf_mapping}")
    print(f"Derived Earthquake -> Merged mapping: {eq_mapping}")
    print(f"Derived Flood      -> Merged mapping: {fl_mapping}")

    # Setup merged dataset directory
    merged_dir = base_dir / "ai" / "datasets" / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)

    splits = ["train", "valid", "test"]
    stats = {s: {"wf_images": 0, "eq_images": 0, "fl_images": 0, "total_boxes": 0} for s in splits}

    for split in splits:
        split_img_dir = merged_dir / split / "images"
        split_lbl_dir = merged_dir / split / "labels"
        split_img_dir.mkdir(parents=True, exist_ok=True)
        split_lbl_dir.mkdir(parents=True, exist_ok=True)

        # 1. Process wildfire
        wf_img_dir = base_dir / split / "images"
        wf_lbl_dir = base_dir / split / "labels"
        if wf_img_dir.exists():
            for img_path in wf_img_dir.iterdir():
                if img_path.is_file() and img_path.suffix.lower() in [".jpg", ".jpeg", ".png"]:
                    merged_img_name = f"wf_{img_path.name}"
                    merged_img_path = split_img_dir / merged_img_name
                    if not merged_img_path.exists():
                        os.symlink(img_path.resolve(), merged_img_path)

                    lbl_path = wf_lbl_dir / f"{img_path.stem}.txt"
                    merged_lbl_path = split_lbl_dir / f"wf_{img_path.stem}.txt"
                    remapped_lines = []
                    if lbl_path.exists():
                        with open(lbl_path, "r", encoding="utf-8") as lf:
                            for line in lf:
                                parts = line.strip().split()
                                if parts and parts[0] in wf_mapping:
                                    new_cls = wf_mapping[parts[0]]
                                    box_line = format_box_line(new_cls, parts[1:])
                                    if box_line:
                                        remapped_lines.append(box_line)

                    with open(merged_lbl_path, "w", encoding="utf-8") as out_f:
                        out_f.writelines(remapped_lines)

                    stats[split]["wf_images"] += 1
                    stats[split]["total_boxes"] += len(remapped_lines)

        # 2. Process earthquake
        eq_img_dir = base_dir / "ai" / "datasets" / "earthquake" / split / "images"
        eq_lbl_dir = base_dir / "ai" / "datasets" / "earthquake" / split / "labels"
        if eq_img_dir.exists():
            for img_path in eq_img_dir.iterdir():
                if img_path.is_file() and img_path.suffix.lower() in [".jpg", ".jpeg", ".png"]:
                    merged_img_name = f"eq_{img_path.name}"
                    merged_img_path = split_img_dir / merged_img_name
                    if not merged_img_path.exists():
                        os.symlink(img_path.resolve(), merged_img_path)

                    lbl_path = eq_lbl_dir / f"{img_path.stem}.txt"
                    merged_lbl_path = split_lbl_dir / f"eq_{img_path.stem}.txt"
                    remapped_lines = []
                    if lbl_path.exists():
                        with open(lbl_path, "r", encoding="utf-8") as lf:
                            for line in lf:
                                parts = line.strip().split()
                                if parts and parts[0] in eq_mapping:
                                    new_cls = eq_mapping[parts[0]]
                                    box_line = format_box_line(new_cls, parts[1:])
                                    if box_line:
                                        remapped_lines.append(box_line)

                    with open(merged_lbl_path, "w", encoding="utf-8") as out_f:
                        out_f.writelines(remapped_lines)

                    stats[split]["eq_images"] += 1
                    stats[split]["total_boxes"] += len(remapped_lines)

        # 3. Process flood
        fl_img_dir = base_dir / "ai" / "datasets" / "flood" / split / "images"
        fl_lbl_dir = base_dir / "ai" / "datasets" / "flood" / split / "labels"
        if fl_img_dir.exists():
            for img_path in fl_img_dir.iterdir():
                if img_path.is_file() and img_path.suffix.lower() in [".jpg", ".jpeg", ".png"]:
                    merged_img_name = f"fl_{img_path.name}"
                    merged_img_path = split_img_dir / merged_img_name
                    if not merged_img_path.exists():
                        os.symlink(img_path.resolve(), merged_img_path)

                    lbl_path = fl_lbl_dir / f"{img_path.stem}.txt"
                    merged_lbl_path = split_lbl_dir / f"fl_{img_path.stem}.txt"
                    remapped_lines = []
                    if lbl_path.exists():
                        with open(lbl_path, "r", encoding="utf-8") as lf:
                            for line in lf:
                                parts = line.strip().split()
                                if parts and parts[0] in fl_mapping:
                                    new_cls = fl_mapping[parts[0]]
                                    box_line = format_box_line(new_cls, parts[1:])
                                    if box_line:
                                        remapped_lines.append(box_line)

                    with open(merged_lbl_path, "w", encoding="utf-8") as out_f:
                        out_f.writelines(remapped_lines)

                    stats[split]["fl_images"] += 1
                    stats[split]["total_boxes"] += len(remapped_lines)

    # 4. Create skyedge/pi/ai/datasets/merged_data.yaml
    merged_yaml_path = base_dir / "ai" / "datasets" / "merged_data.yaml"
    merged_config = {
        "path": str(merged_dir),
        "train": "train/images",
        "val": "valid/images",
        "test": "test/images",
        "nc": len(TARGET_CLASSES),
        "names": TARGET_CLASSES,
    }

    with open(merged_yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged_config, f, sort_keys=False)

    print(f"Created merged YAML at: {merged_yaml_path}")
    print("Merged Dataset Summary:")
    for s, data in stats.items():
        total_imgs = data['wf_images'] + data['eq_images'] + data['fl_images']
        print(
            f"  {s}: {data['wf_images']} wildfire + {data['eq_images']} earthquake + {data['fl_images']} flood "
            f"= {total_imgs} total images, {data['total_boxes']} annotations"
        )

    return merged_yaml_path


if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent.parent
    prepare_merged_dataset(base_dir)
