import os
from pathlib import Path
import cv2


def compress(path, quality, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"Could not read image from path: {path}")
    filename = Path(path).name
    output_path = os.path.join(output_dir, filename)
    success = cv2.imwrite(output_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not success:
        raise IOError(f"Failed to write compressed image to: {output_path}")
    return output_path
