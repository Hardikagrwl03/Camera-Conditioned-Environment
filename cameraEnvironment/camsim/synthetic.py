"""Synthetic scenes / raw frames (no device, no DNG needed). Used by tests and the smoke run."""
from __future__ import annotations

import numpy as np

from .io.dng import RawFrame


def make_scene_rgb(h: int, w: int, seed: int = 0) -> np.ndarray:
    """Smooth, textured linear-RGB scene in [0.02, 0.9], shape (h, w, 3)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    yy, xx = yy / h, xx / w
    base = 0.25 + 0.35 * xx[..., None] * np.array([1.0, 0.8, 0.5]) + 0.2 * yy[..., None] * np.array([0.3, 0.7, 1.0])
    for _ in range(6):
        cy, cx, r = rng.uniform(0.1, 0.9), rng.uniform(0.1, 0.9), rng.uniform(0.05, 0.2)
        m = ((yy - cy) ** 2 + (xx - cx) ** 2 < r * r)[..., None]
        base = np.where(m, rng.uniform(0.05, 0.85, 3)[None, None], base)
    stripes = 0.05 * np.sin(2 * np.pi * (xx * 18 + yy * 7))[..., None]
    return np.clip(base + stripes, 0.02, 0.9).astype(np.float32)


def mosaic(rgb: np.ndarray, cfa: str = "RGGB") -> np.ndarray:
    h, w, _ = rgb.shape
    out = np.zeros((h, w), np.float32)
    for k, ch in enumerate(cfa):
        out[k // 2::2, k % 2::2] = rgb[k // 2::2, k % 2::2, "RGB".index(ch)]
    return out


def make_raw_frame(h=128, w=160, iso=50.0, exposure_s=0.1, gain=0.25, read_e=3.0, full_e=3600.0,
                   black=64.0, white=1023.0, cfa="RGGB", seed=0, wb=(2.0, 1.0, 1.0, 1.6),
                   scene_rgb=None, noisy=True, focus_d=1.0) -> RawFrame:
    """A physically generated raw frame: Poisson shot noise + Gaussian read noise + 10-bit ADC.

    ``full_e`` is the electron count a unit-radiance pixel collects at ``exposure_s``.
    """
    rng = np.random.default_rng(seed)
    rgb = make_scene_rgb(h, w, 1234) if scene_rgb is None else scene_rgb
    # per-channel sensitivity so that the WB gains neutralise a grey scene
    sens = np.array([1 / wb[0], 1.0, 1 / wb[3]], np.float32)
    m = mosaic(rgb * sens, cfa)
    mu = m * full_e
    e = rng.poisson(mu).astype(np.float64) if noisy else mu
    d = gain * e + (rng.normal(0, read_e * gain, mu.shape) if noisy else 0.0)
    d = np.round(d) if noisy else d
    bayer = np.clip(d + black, 0, white)
    rng_dn = white - black
    S = gain / rng_dn
    O = (read_e * gain) ** 2 / rng_dn ** 2
    return RawFrame(bayer=bayer, cfa=cfa, black_2x2=np.full((2, 2), black), white_level=white, iso=iso,
                    exposure_s=exposure_s, focus_d=focus_d, noise_profile=np.tile([[S, O]], (4, 1)),
                    wb_gains=tuple(wb), ccm=np.eye(3))


def write_synthetic_dng(path, frame: RawFrame, noise_profile=True):
    """Write a minimal uncompressed CFA DNG with ``tifffile`` (tags used by ``load_dng``)."""
    import tifffile
    cfa_codes = [{"R": 0, "G": 1, "B": 2}[c] for c in frame.cfa]
    extratags = [
        (33421, "H", 2, (2, 2), True),
        (33422, "B", 4, tuple(cfa_codes), True),
        (50714, "H", 4, tuple(int(v) for v in frame.black_2x2.reshape(-1)), True),
        (50713, "H", 2, (2, 2), True),
        (50717, "I", 1, int(frame.white_level), True),
        (50728, "d", 3, (0.5, 1.0, 0.625), True),
        (50721, "2i", 9, (1000, 1000, 0, 1, 0, 1, 0, 1, 1000, 1000, 0, 1, 0, 1, 0, 1, 1000, 1000), True),
        (50964, "2i", 9, (1000, 1000, 0, 1, 0, 1, 0, 1, 1000, 1000, 0, 1, 0, 1, 0, 1, 1000, 1000), True),
        (50778, "H", 1, 21, True),
    ]
    if noise_profile and frame.noise_profile is not None:
        npf = np.asarray(frame.noise_profile, float).reshape(-1)
        extratags.append((51041, "d", int(npf.size), tuple(npf), True))
    tifffile.imwrite(str(path), frame.bayer.astype(np.uint16), photometric="cfa", extratags=extratags)
