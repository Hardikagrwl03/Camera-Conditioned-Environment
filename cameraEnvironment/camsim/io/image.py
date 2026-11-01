"""Minimal image read / write with graceful optional-dependency handling."""
from __future__ import annotations

from pathlib import Path

import numpy as np


def read_image_rgb(path) -> np.ndarray:
    """Read a PNG/TIFF as uint8 (or uint16) HxWx3 RGB, dropping any alpha channel."""
    path = str(path)
    try:
        import cv2
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise IOError(f"cv2 could not read {path}")
        if img.ndim == 2:
            img = np.stack([img] * 3, -1)
        elif img.shape[2] == 4:
            img = img[..., :3]
        return np.ascontiguousarray(img[..., ::-1])  # BGR -> RGB
    except ImportError:
        pass
    try:
        from PIL import Image
        return np.asarray(Image.open(path).convert("RGB"))
    except ImportError as e:
        raise ImportError("Reading images needs opencv-python-headless or Pillow "
                          "(pip install -r requirements.txt)") from e


def write_png_rgba(path, rgb_u8: np.ndarray) -> None:
    """Write 8-bit RGB as RGBA PNG with alpha 255, exactly like the Lattice app's sweep PNGs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rgba = np.concatenate([rgb_u8, np.full(rgb_u8.shape[:2] + (1,), 255, np.uint8)], -1)
    try:
        import cv2
        ok = cv2.imwrite(str(path), np.ascontiguousarray(rgba[..., [2, 1, 0, 3]]))
        if not ok:
            raise IOError(f"cv2 failed to write {path}")
        return
    except ImportError:
        pass
    from PIL import Image
    Image.fromarray(rgba, "RGBA").save(path)


def write_tiff16(path, arr: np.ndarray) -> None:
    import tifffile
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), np.clip(np.rint(arr), 0, 65535).astype(np.uint16))
