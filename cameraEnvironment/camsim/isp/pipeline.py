"""ISP model (torch): simulated raw -> the 8-bit RGBA values of a Lattice sweep PNG.

Stages: black level -> lens-shading gain map -> WB gains -> demosaic (Malvar-He-Cutler) -> CCM ->
alignment warp -> tone LUT -> [optional learned residual] -> 8-bit RGB -> BT.601 limited-range YUV
-> 4:2:0 chroma -> exact integer ``yuv420ToArgb`` replica -> exact integer ``boxDownscale``
replica. The last two replicate ``YuvConverter.kt`` / ``Downscaler.kt`` bit for bit.
"""
from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import torch
import torch.nn.functional as F

from ..align import Alignment

LUT_SIZE = 1024
# XYZ(D50) -> linear sRGB (Bradford-adapted, Lindbloom)
XYZ_D50_TO_SRGB = np.array([[3.1338561, -1.6168667, -0.4906146],
                            [-0.9787684, 1.9161415, 0.0334540],
                            [0.0719453, -0.2289914, 1.4052427]])
# XYZ(D65) -> linear sRGB
XYZ_D65_TO_SRGB = np.array([[3.2404542, -1.5371385, -0.4985314],
                            [-0.9692660, 1.8760108, 0.0415560],
                            [0.0556434, -0.2040259, 1.0572252]])
ILLUMINANT_D65, ILLUMINANT_A = 21, 17


def srgb_oetf_lut(n: int = LUT_SIZE) -> np.ndarray:
    """Default tone curve (sRGB OETF) over u = sqrt(linear), output 0..255. (3, n)."""
    u = np.linspace(0, 1, n)
    lin = u ** 2
    v = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    return np.tile((255.0 * v)[None], (3, 1))


@dataclass
class ISPParams:
    cfa: str = "RGGB"
    black_2x2: np.ndarray = field(default_factory=lambda: np.zeros((2, 2)))
    white: float = 1023.0
    shading: Optional[np.ndarray] = None            # (gh, gw, 4) gain per CFA position
    ccm: np.ndarray = field(default_factory=lambda: np.eye(3))
    tone_lut: np.ndarray = field(default_factory=srgb_oetf_lut)    # (3, N) over sqrt(linear)
    chroma: str = "average"                          # "average" | "topleft"
    post_raw_boost: float = 100.0
    source: str = "default"
    notes: List[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"cfa": self.cfa, "black_2x2": np.asarray(self.black_2x2).tolist(), "white": self.white,
                "shading": None if self.shading is None else np.asarray(self.shading).tolist(),
                "ccm": np.asarray(self.ccm).tolist(), "tone_lut": np.asarray(self.tone_lut).tolist(),
                "chroma": self.chroma, "post_raw_boost": self.post_raw_boost, "source": self.source,
                "notes": self.notes}

    @classmethod
    def from_json(cls, d) -> "ISPParams":
        return cls(d["cfa"], np.asarray(d["black_2x2"]), d["white"],
                   None if d.get("shading") is None else np.asarray(d["shading"], np.float32),
                   np.asarray(d["ccm"]), np.asarray(d["tone_lut"]), d.get("chroma", "average"),
                   d.get("post_raw_boost", 100.0), d.get("source", "file"), d.get("notes", []))

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_json()))

    @classmethod
    def load(cls, path):
        return cls.from_json(json.loads(Path(path).read_text()))


def ccm_from_frame(frame) -> (np.ndarray, str):
    """Camera (white-balanced) RGB -> linear sRGB, best available source."""
    if frame.ccm is not None:
        return np.asarray(frame.ccm, float), "HAL COLOR_CORRECTION_TRANSFORM from sidecar"
    if frame.forward_matrix:
        ill = ILLUMINANT_D65 if ILLUMINANT_D65 in frame.forward_matrix else sorted(frame.forward_matrix)[0]
        return XYZ_D50_TO_SRGB @ frame.forward_matrix[ill], f"DNG ForwardMatrix (illuminant {ill})"
    if frame.color_matrix:
        ill = ILLUMINANT_D65 if ILLUMINANT_D65 in frame.color_matrix else sorted(frame.color_matrix)[0]
        g = frame.wb_gains or (1.0, 1.0, 1.0, 1.0)
        m = XYZ_D65_TO_SRGB @ np.linalg.inv(frame.color_matrix[ill]) @ np.diag(1.0 / np.array([g[0], 1.0, g[3]]))
        m = m / m.sum(1, keepdims=True)
        return m, f"DNG ColorMatrix (illuminant {ill}) inverted, rows normalised"
    return np.eye(3), "identity (no colour metadata)"


def default_isp_params(frame) -> ISPParams:
    ccm, src = ccm_from_frame(frame)
    p = ISPParams(frame.cfa, frame.black_2x2.copy(), frame.white_level, frame.gain_map, ccm,
                  post_raw_boost=frame.post_raw_boost, source="default")
    p.notes.append(f"CCM: {src}")
    p.notes.append("tone curve: sRGB OETF fallback (NOT fitted); expect large brightness errors")
    if frame.gain_map is None:
        p.notes.append("no lens-shading gain map in the DNG / calibration")
    return p


# ----------------------------------------------------------------------------------------------
# exact integer replicas of the Android code
# ----------------------------------------------------------------------------------------------
def yuv420_to_rgb_int(y: torch.Tensor, u: torch.Tensor, v: torch.Tensor) -> torch.Tensor:
    """Exact replica of YuvConverter.kt ``yuv420ToArgb``.

    ``y`` (B,H,W) int 0..255; ``u``,``v`` (B,ceil(H/2),ceil(W/2)). Chroma is upsampled by
    nearest neighbour (``col shr 1``, ``row shr 1``). Returns (B,3,H,W) int32 in 0..255 (R,G,B).
    """
    H, W = y.shape[-2:]
    y = y.to(torch.int32)
    u = u.to(torch.int32).repeat_interleave(2, -2).repeat_interleave(2, -1)[..., :H, :W] - 128
    v = v.to(torch.int32).repeat_interleave(2, -2).repeat_interleave(2, -1)[..., :H, :W] - 128
    y1192 = 1192 * (y - 16).clamp_min(0)
    r = torch.bitwise_right_shift(y1192 + 1634 * v, 10)
    g = torch.bitwise_right_shift(y1192 - 833 * v - 400 * u, 10)
    b = torch.bitwise_right_shift(y1192 + 2066 * u, 10)
    return torch.stack([r, g, b], -3).clamp_(0, 255)


def box_downscale_int(rgb: torch.Tensor, factor: int) -> torch.Tensor:
    """Exact replica of Downscaler.kt ``boxDownscale`` on (B,3,H,W) ints: ``(sum + n/2) / n``."""
    if factor <= 1:
        return rgb
    H, W = rgb.shape[-2:]
    oh, ow = H // factor, W // factor
    if oh <= 0 or ow <= 0:
        raise ValueError(f"downscale {factor}x leaves no pixels for {W}x{H}")
    x = rgb[..., :oh * factor, :ow * factor].to(torch.int64)
    x = x.reshape(*x.shape[:-2], oh, factor, ow, factor).sum((-3, -1))
    n = factor * factor
    return ((x + n // 2) // n).to(torch.int32)


def rgb_to_yuv420(rgb255: torch.Tensor, chroma: str = "average"):
    """BT.601 limited-range encode of integer-valued RGB (B,3,H,W) -> int Y (B,H,W), U,V (B,H/2,W/2)."""
    r, g, b = rgb255[:, 0], rgb255[:, 1], rgb255[:, 2]
    y = 16 + (65.481 * r + 128.553 * g + 24.966 * b) / 255.0
    cb = 128 + (-37.797 * r - 74.203 * g + 112.0 * b) / 255.0
    cr = 128 + (112.0 * r - 93.786 * g - 18.214 * b) / 255.0
    H, W = y.shape[-2:]
    if H % 2 or W % 2:
        cb = F.pad(cb[:, None], (0, W % 2, 0, H % 2), mode="replicate")[:, 0]
        cr = F.pad(cr[:, None], (0, W % 2, 0, H % 2), mode="replicate")[:, 0]
    if chroma == "average":
        cb = F.avg_pool2d(cb[:, None], 2)[:, 0]
        cr = F.avg_pool2d(cr[:, None], 2)[:, 0]
    elif chroma == "topleft":
        cb, cr = cb[:, ::2, ::2], cr[:, ::2, ::2]
    else:
        raise ValueError(chroma)
    q = lambda t: torch.round(t).clamp_(0, 255).to(torch.int32)
    return q(y), q(cb), q(cr)


# ----------------------------------------------------------------------------------------------
# demosaic
# ----------------------------------------------------------------------------------------------
def _mhc_kernels() -> torch.Tensor:
    kg = np.array([[0, 0, -1, 0, 0], [0, 0, 2, 0, 0], [-1, 2, 4, 2, -1], [0, 0, 2, 0, 0], [0, 0, -1, 0, 0]], np.float32) / 8
    kh = np.array([[0, 0, .5, 0, 0], [0, -1, 0, -1, 0], [-1, 4, 5, 4, -1], [0, -1, 0, -1, 0], [0, 0, .5, 0, 0]], np.float32) / 8
    kv = kh.T.copy()
    kx = np.array([[0, 0, -1.5, 0, 0], [0, 2, 0, 2, 0], [-1.5, 0, 6, 0, -1.5], [0, 2, 0, 2, 0], [0, 0, -1.5, 0, 0]], np.float32) / 8
    return torch.from_numpy(np.stack([kg, kh, kv, kx]))[:, None]


def demosaic_mhc(mosaic: torch.Tensor, cfa: str) -> torch.Tensor:
    """Malvar-He-Cutler 5x5 demosaic for any 2x2 CFA. (B,H,W) -> (B,3,H,W)."""
    B, H, W = mosaic.shape
    x = mosaic[:, None]
    conv = F.conv2d(F.pad(x, (2, 2, 2, 2), mode="reflect"), _mhc_kernels().to(x.device, x.dtype))
    full = torch.cat([x, conv], 1)                       # 0 own, 1 G@RB, 2 X@G horiz, 3 X@G vert, 4 X@opposite
    chans = "RGB"
    idx = np.zeros((3, 2, 2), np.int64)
    for c, cname in enumerate(chans):
        for k, site in enumerate(cfa):
            i, j = k // 2, k % 2
            if site == cname:
                sel = 0
            elif cname == "G":
                sel = 1
            elif site == "G":
                horiz_neighbour = cfa[i * 2 + (1 - j)]
                sel = 2 if horiz_neighbour == cname else 3
            else:
                sel = 4
            idx[c, i, j] = sel
    idx_t = torch.from_numpy(np.tile(idx, (1, (H + 1) // 2, (W + 1) // 2))[:, :H, :W]).to(x.device)
    out = [torch.gather(full, 1, idx_t[c].view(1, 1, H, W).expand(B, 1, H, W)) for c in range(3)]
    return torch.cat(out, 1)


# ----------------------------------------------------------------------------------------------
class ISPModel:
    def __init__(self, params: ISPParams, device="cpu", alignment: Optional[Alignment] = None, residual=None):
        self.params = params
        self.device = torch.device(device)
        self.alignment = alignment or Alignment()
        self.residual = residual                       # optional ResidualNet, applied in the gamma domain
        self._shade_cache = {}

    # ------------------------------------------------------------- helpers
    def _shading_map(self, H, W) -> Optional[torch.Tensor]:
        p = self.params
        if p.shading is None:
            return None
        key = (H, W)
        if key in self._shade_cache:
            return self._shade_cache[key]
        g = torch.as_tensor(p.shading, dtype=torch.float32, device=self.device).permute(2, 0, 1)[None]  # 1,4,gh,gw
        up = F.interpolate(g, size=((H + 1) // 2, (W + 1) // 2), mode="bilinear", align_corners=True)[0]
        m = torch.ones(2 * up.shape[1], 2 * up.shape[2], device=self.device)
        for k in range(4):
            m[k // 2::2, k % 2::2] = up[k]
        m = m[:H, :W]
        self._shade_cache[key] = m
        return m

    def wb_tensor(self, wb, B) -> torch.Tensor:
        """Per-position gains (B,2,2) from HAL-style [R, Gr(even), Gb(odd), B] gains."""
        w = torch.as_tensor(np.asarray(wb, np.float32), device=self.device)
        if w.ndim == 1:
            w = w.expand(B, 4)
        cfa = self.params.cfa
        r_row = cfa.index("R") // 2
        cols = []
        for k, ch in enumerate(cfa):
            i = k // 2
            cols.append(w[:, 0] if ch == "R" else w[:, 3] if ch == "B" else (w[:, 1] if i == r_row else w[:, 2]))
        return torch.stack(cols, 1).view(B, 2, 2)

    # ------------------------------------------------------------- stages
    @torch.no_grad()
    def cam_rgb(self, raw: torch.Tensor, wb) -> torch.Tensor:
        """Raw (B,H,W) DN -> white-balanced, demosaiced camera RGB (B,3,H,W), before the CCM."""
        p = self.params
        raw = raw.to(self.device, torch.float32)
        B, H, W = raw.shape
        black = torch.as_tensor(np.tile(np.asarray(p.black_2x2, np.float32), ((H + 1) // 2, (W + 1) // 2))[:H, :W],
                                device=self.device)
        x = ((raw - black) / float(p.white - float(np.mean(p.black_2x2)))).clamp_min_(0.0)
        sh = self._shading_map(H, W)
        if sh is not None:
            x = x * sh
        wbp = self.wb_tensor(wb, B)
        wmap = wbp.repeat(1, (H + 1) // 2, (W + 1) // 2)[:, :H, :W]
        x = x * wmap * (p.post_raw_boost / 100.0)
        return demosaic_mhc(x, p.cfa)

    def apply_ccm(self, rgb: torch.Tensor) -> torch.Tensor:
        ccm = torch.as_tensor(np.asarray(self.params.ccm, np.float32), device=self.device)
        return torch.einsum("ij,bjhw->bihw", ccm, rgb).clamp_min_(0.0)

    @torch.no_grad()
    def linear_rgb(self, raw: torch.Tensor, wb, out_hw=None) -> torch.Tensor:
        """Raw (B,H,W) DN -> linear sRGB (B,3,Ho,Wo) on the PNG grid (clamped >= 0)."""
        rgb = self.alignment.warp(self.cam_rgb(raw, wb), out_hw)
        return self.apply_ccm(rgb)

    def tone(self, lin: torch.Tensor) -> torch.Tensor:
        """Per-channel tone LUT over u=sqrt(linear): (B,3,H,W) linear -> 0..255 float."""
        lut = torch.as_tensor(np.asarray(self.params.tone_lut, np.float32), device=self.device)   # 3,N
        n = lut.shape[1]
        u = lin.clamp(0.0, 1.0).sqrt_() * (n - 1)
        i0 = u.floor().clamp_(0, n - 2)
        f = u - i0
        i0 = i0.long()
        out = torch.empty_like(lin)
        for c in range(3):
            out[:, c] = lut[c][i0[:, c]] * (1 - f[:, c]) + lut[c][i0[:, c] + 1] * f[:, c]
        return out

    def encode_decode(self, rgb255: torch.Tensor) -> torch.Tensor:
        """Float 0..255 RGB -> what the app writes: 8-bit RGB through YUV 4:2:0 and the exact decoder."""
        rgb = torch.round(rgb255).clamp_(0, 255)
        y, u, v = rgb_to_yuv420(rgb, self.params.chroma)
        return yuv420_to_rgb_int(y, u, v)

    @torch.no_grad()
    def __call__(self, raw: torch.Tensor, wb, downscale: int = 1, iso: Optional[Sequence[float]] = None,
                 exposure: Optional[Sequence[float]] = None, out_hw=None) -> torch.Tensor:
        """Raw (B,H,W) -> int32 (B,3,h,w): the PNG pixel values (RGB), after the app's downscale."""
        lin = self.linear_rgb(raw, wb, out_hw)
        g = self.tone(lin)
        if self.residual is not None:
            g = self.residual(g / 255.0, iso, exposure) * 255.0
        return box_downscale_int(self.encode_decode(g), downscale)
