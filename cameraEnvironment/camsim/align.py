"""Geometric mapping between the raw-ISP output and the sweep PNG.

The matrix ``M`` maps *output* (PNG, full-resolution) pixel indices to *resampled-input* pixel
indices, i.e. ``png(x) ~ sim(S @ M @ x)`` where ``S`` is the pure size-ratio scaling between the
simulated image and the PNG grid. Keeping ``S`` separate makes the saved alignment independent of
the raw resolution. Indices have pixel centres at integers (OpenCV convention).
"""
from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import torch
import torch.nn.functional as F


def _size_scaling(in_hw, out_hw) -> np.ndarray:
    (hi, wi), (ho, wo) = in_hw, out_hw
    sx, sy = wi / wo, hi / ho
    return np.array([[sx, 0, 0.5 * sx - 0.5], [0, sy, 0.5 * sy - 0.5], [0, 0, 1]], np.float64)


@dataclass
class Alignment:
    matrix: np.ndarray = field(default_factory=lambda: np.eye(3))
    out_hw: Optional[Tuple[int, int]] = None      # PNG full-resolution (H, W); None = same as input
    residual_px: Optional[float] = None
    notes: list = field(default_factory=list)

    def is_identity(self, in_hw) -> bool:
        return (np.allclose(self.matrix, np.eye(3), atol=1e-9)
                and (self.out_hw is None or tuple(self.out_hw) == tuple(in_hw)))

    def to_json(self) -> dict:
        return {"matrix": np.asarray(self.matrix).tolist(), "out_hw": list(self.out_hw) if self.out_hw else None,
                "residual_px": self.residual_px, "notes": self.notes}

    @classmethod
    def from_json(cls, d) -> "Alignment":
        return cls(np.asarray(d["matrix"], np.float64), tuple(d["out_hw"]) if d.get("out_hw") else None,
                   d.get("residual_px"), d.get("notes", []))

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_json(), indent=2))

    @classmethod
    def load(cls, path):
        return cls.from_json(json.loads(Path(path).read_text()))

    def total_matrix(self, in_hw) -> np.ndarray:
        out_hw = self.out_hw or tuple(in_hw)
        return _size_scaling(tuple(in_hw), tuple(out_hw)) @ self.matrix

    def warp(self, img: torch.Tensor, out_hw: Optional[Tuple[int, int]] = None) -> torch.Tensor:
        """Resample (B,C,Hin,Win) to the PNG grid (bilinear, border-replicated)."""
        hin, win = img.shape[-2:]
        if out_hw is None:
            out_hw = self.out_hw or (hin, win)
        out_hw = tuple(int(v) for v in out_hw)
        if np.allclose(self.matrix, np.eye(3), atol=1e-12) and out_hw == (hin, win):
            return img
        M = torch.as_tensor(_size_scaling((hin, win), out_hw) @ self.matrix, dtype=torch.float32, device=img.device)
        ho, wo = out_hw
        ys, xs = torch.meshgrid(torch.arange(ho, device=img.device, dtype=torch.float32),
                                torch.arange(wo, device=img.device, dtype=torch.float32), indexing="ij")
        xi = M[0, 0] * xs + M[0, 1] * ys + M[0, 2]
        yi = M[1, 0] * xs + M[1, 1] * ys + M[1, 2]
        w = M[2, 0] * xs + M[2, 1] * ys + M[2, 2]
        xi, yi = xi / w, yi / w
        grid = torch.stack([(2 * xi + 1) / win - 1, (2 * yi + 1) / hin - 1], -1).unsqueeze(0)
        grid = grid.expand(img.shape[0], -1, -1, -1)
        return F.grid_sample(img, grid, mode="bilinear", padding_mode="border", align_corners=False)


def _prep(gray: np.ndarray) -> np.ndarray:
    """Tone-invariant feature image: blurred gradient magnitude, normalised."""
    import cv2
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.5)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    m = cv2.GaussianBlur(np.sqrt(gx * gx + gy * gy), (0, 0), 1.5)
    m = m - m.mean()
    return m / (m.std() + 1e-9)


def _ncc(ref: np.ndarray, sim: np.ndarray, M: np.ndarray) -> float:
    import cv2
    h, w = ref.shape
    warped = cv2.warpPerspective(sim, M.astype(np.float64), (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                 borderMode=cv2.BORDER_REPLICATE)
    m = max(4, min(h, w) // 16)
    a, b = ref[m:-m, m:-m], warped[m:-m, m:-m]
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-12))


def _plausible(M: np.ndarray, motion: str, shape) -> bool:
    h, w = shape
    if abs(M[0, 2]) > 0.1 * w or abs(M[1, 2]) > 0.1 * h:
        return False
    if motion == "translation":
        return True
    ok = abs(M[0, 0] - 1) < 0.1 and abs(M[1, 1] - 1) < 0.1 and abs(M[0, 1]) < 0.05 and abs(M[1, 0]) < 0.05
    if motion == "homography":
        ok = ok and abs(M[2, 0]) < 1e-3 and abs(M[2, 1]) < 1e-3
    return ok


def _ecc_pyramid(ref, sim, M0, mt, motion, levels, iters, eps):
    import cv2
    ho, wo = ref.shape
    M = M0.copy()
    for lvl in reversed(range(levels)):
        s = 2 ** lvl
        if min(ho, wo) // s < 40:
            continue
        C = np.array([[1 / s, 0, 0.5 / s - 0.5], [0, 1 / s, 0.5 / s - 0.5], [0, 0, 1]])
        Ci = np.linalg.inv(C)
        r = cv2.resize(ref, (wo // s, ho // s), interpolation=cv2.INTER_AREA) if s > 1 else ref
        i = cv2.resize(sim, (wo // s, ho // s), interpolation=cv2.INTER_AREA) if s > 1 else sim
        Ml = C @ M @ Ci
        W = Ml.astype(np.float32) if motion == "homography" else Ml[:2].astype(np.float32)
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, eps)
        _, W = cv2.findTransformECC(r, i, W, mt, crit, None, 5)
        Ml = W if W.shape[0] == 3 else np.vstack([W, [0, 0, 1]])
        M = Ci @ Ml.astype(np.float64) @ C
    return M


def estimate_alignment(sim_gray: np.ndarray, ref_gray: np.ndarray, motion: str = "affine",
                       levels: int = 4, iters: int = 80, eps: float = 1e-5) -> Alignment:
    """Estimate the alignment of a simulated image to a reference PNG.

    ECC on a pyramid of tone-invariant gradient-magnitude images, first translation-only, then the
    requested model. A model is accepted only if its normalised cross-correlation beats the
    previous best by a margin AND its parameters are physically plausible (|scale-1| < 10 %,
    |shear| < 5 %, |shift| < 10 % of the frame); otherwise the identity (pure size scaling) stays,
    with a note. Both images are resampled to the reference grid first.
    """
    import cv2
    ho, wo = ref_gray.shape
    hs, ws = sim_gray.shape
    sim_r = cv2.resize(sim_gray.astype(np.float32), (wo, ho), interpolation=cv2.INTER_AREA if ws > wo else cv2.INTER_LINEAR)
    ref, sim = _prep(ref_gray), _prep(sim_r)
    best_M, best = np.eye(3), _ncc(ref, sim, np.eye(3))
    notes = [f"identity NCC {best:.4f}"]
    models = ["translation"] + ([motion] if motion != "translation" else [])
    mt_of = {"translation": cv2.MOTION_TRANSLATION, "affine": cv2.MOTION_AFFINE, "homography": cv2.MOTION_HOMOGRAPHY}
    for mo in models:
        try:
            M = _ecc_pyramid(ref, sim, best_M, mt_of[mo], mo, levels, iters, eps)
        except cv2.error as e:
            notes.append(f"{mo} ECC failed ({str(e).splitlines()[0][:60]})")
            continue
        if not _plausible(M, mo, ref.shape):
            notes.append(f"{mo} rejected: implausible parameters")
            continue
        c = _ncc(ref, sim, M)
        if c > best + 0.02:
            best_M, best = M, c
            notes.append(f"{mo} accepted, NCC {c:.4f}")
        else:
            notes.append(f"{mo} rejected: NCC {c:.4f} not better than {best:.4f}")
    if len(notes) == 1 or all(("rejected" in n or "failed" in n) for n in notes[1:]):
        warnings.warn("alignment: identity kept (" + "; ".join(notes) + ")")
    al = Alignment(best_M, (ho, wo), None, notes)
    al.residual_px = residual_shift(sim_gray, ref_gray, al)
    return al


def residual_shift(sim_gray, ref_gray, al: Alignment) -> float:
    """Remaining misalignment (px, magnitude of the phase-correlation peak) after applying ``al``."""
    import cv2
    ho, wo = ref_gray.shape
    t = torch.from_numpy(np.ascontiguousarray(sim_gray, dtype=np.float32))[None, None]
    warped = al.warp(t, (ho, wo))[0, 0].numpy()
    a, b = _prep(warped), _prep(ref_gray)   # gradient-magnitude features
    win = cv2.createHanningWindow((wo, ho), cv2.CV_32F)
    (dx, dy), _ = cv2.phaseCorrelate(a.astype(np.float32), b.astype(np.float32), win)
    return float(np.hypot(dx, dy))
