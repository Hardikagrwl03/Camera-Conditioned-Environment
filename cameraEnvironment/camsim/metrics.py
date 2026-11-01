"""Image metrics used by validation, including the Neural3A reward terms.

The reward formulas are re-implemented from
``Neural-3A-SAC/src/neural3a/rewards/reward.py`` (no import dependency):

* ``midtone_brightness(Y) = 1 - mean(|Y - 0.5|)``
* ``sobel_reward``: mean 3x3-Sobel gradient magnitude ``m``; bounded reward ``2 m / (m + Ks)``, Ks = 5
* ``tenengrad_reward``: ``mean(Gx^2 + Gy^2) / 1e5`` with a 9x9 Sobel (OpenCV ``ksize=9`` kernels),
  bounded reward ``2 t / (t + Kt)``, Kt = 25
* crop convention of ``camera_env.py``: ``offset = 150`` centre crop of the frame, Y = 0.299R +
  0.587G + 0.114B on [0,1] RGB (``normalize_rewards = False`` path).
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import numpy as np
import torch
import torch.nn.functional as F

CROP = 150
KS, KT = 5.0, 25.0


# ----------------------------------------------------------------------------------------------
# colour / brightness
# ----------------------------------------------------------------------------------------------
def srgb_to_linear(u8: torch.Tensor) -> torch.Tensor:
    v = u8.float() / 255.0
    return torch.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def ev_error(sim_u8: torch.Tensor, real_u8: torch.Tensor, mask: Optional[torch.Tensor] = None,
             floor: float = 0.004) -> Dict[str, list]:
    """Per-channel mean / median of log2(sim/real) on sRGB-linearised values (stops).

    Pixels where either side is below ``floor`` (linear) are dropped: log-ratios of
    noise-dominated blacks are meaningless. Inputs (3,H,W) uint8-valued. ``mask`` (H,W) True=valid.
    """
    s, r = srgb_to_linear(sim_u8), srgb_to_linear(real_u8)
    ok = (s > floor) & (r > floor)
    if mask is not None:
        ok = ok & mask[None]
    out = {"mean": [], "median": [], "n": []}
    for c in range(3):
        e = torch.log2(s[c][ok[c]] / r[c][ok[c]])
        out["n"].append(int(e.numel()))
        out["mean"].append(float(e.mean()) if e.numel() else float("nan"))
        out["median"].append(float(e.median()) if e.numel() else float("nan"))
    return out


def dn_error(sim_u8, real_u8, mask=None) -> Dict[str, list]:
    d = (sim_u8.float() - real_u8.float())
    out = {"mean": [], "median": []}
    for c in range(3):
        x = d[c][mask] if mask is not None else d[c].reshape(-1)
        out["mean"].append(float(x.mean()) if x.numel() else float("nan"))
        out["median"].append(float(x.median()) if x.numel() else float("nan"))
    return out


def emd_per_channel(sim_u8, real_u8, mask=None) -> list:
    """1-D Earth-mover (Wasserstein-1) distance between 8-bit histograms, in DN."""
    res = []
    for c in range(3):
        a = sim_u8[c][mask] if mask is not None else sim_u8[c].reshape(-1)
        b = real_u8[c][mask] if mask is not None else real_u8[c].reshape(-1)
        if a.numel() == 0 or b.numel() == 0:
            res.append(float("nan"))
            continue
        ha = torch.bincount(a.long().clamp(0, 255), minlength=256).float()
        hb = torch.bincount(b.long().clamp(0, 255), minlength=256).float()
        res.append(float((torch.cumsum(ha / ha.sum(), 0) - torch.cumsum(hb / hb.sum(), 0)).abs().sum()))
    return res


# ----------------------------------------------------------------------------------------------
# pixelwise
# ----------------------------------------------------------------------------------------------
def mae(a, b, mask=None) -> float:
    d = (a.float() - b.float()).abs()
    return float(d[:, mask].mean()) if mask is not None and mask.any() else float(d.mean())


def psnr(a, b, mask=None, peak: float = 255.0) -> float:
    d = (a.float() - b.float()) ** 2
    m = float(d[:, mask].mean()) if mask is not None and mask.any() else float(d.mean())
    return float("inf") if m <= 1e-12 else 10 * math.log10(peak * peak / m)


def _gauss_win(size=11, sigma=1.5, device="cpu"):
    ax = torch.arange(size, dtype=torch.float32, device=device) - size // 2
    g = torch.exp(-ax ** 2 / (2 * sigma ** 2))
    g = g / g.sum()
    return (g[:, None] * g[None, :])[None, None]


def ssim(a, b, mask=None, peak: float = 255.0) -> float:
    """Mean SSIM (Gaussian 11x11, sigma 1.5, per channel, averaged). Inputs (3,H,W)."""
    x, y = a.float()[:, None], b.float()[:, None]
    w = _gauss_win(device=x.device)
    c1, c2 = (0.01 * peak) ** 2, (0.03 * peak) ** 2
    mx, my = F.conv2d(x, w), F.conv2d(y, w)
    sxx = F.conv2d(x * x, w) - mx * mx
    syy = F.conv2d(y * y, w) - my * my
    sxy = F.conv2d(x * y, w) - mx * my
    s = ((2 * mx * my + c1) * (2 * sxy + c2)) / ((mx * mx + my * my + c1) * (sxx + syy + c2))
    s = s[:, 0]
    if mask is not None:
        m = F.max_pool2d((~mask).float()[None, None], 11, 1, 5)[0, 0] < 0.5       # erode
        m = m[5:-5, 5:-5]
        if m.any():
            return float(s[:, m].mean())
    return float(s.mean())


# ----------------------------------------------------------------------------------------------
# noise
# ----------------------------------------------------------------------------------------------
def _hp(gray: torch.Tensor, sigma: float = 1.5):
    r = int(3 * sigma)
    ax = torch.arange(-r, r + 1, dtype=torch.float32)
    k = torch.exp(-ax ** 2 / (2 * sigma ** 2)); k = k / k.sum()
    k2 = (k[:, None] * k[None, :])[None, None]
    blur = F.conv2d(F.pad(gray[None, None], (r, r, r, r), mode="reflect"), k2)[0, 0]
    # variance gain of the high-pass for white noise: (1-k0)^2 + sum(k^2) - k0^2
    k0 = float(k2[0, 0, r, r])
    gain = (1 - k0) ** 2 + float((k2 ** 2).sum()) - k0 ** 2
    return gray - blur, gain


def noise_level_function(rgb_u8: torch.Tensor, bins: int = 12, block: int = 8):
    """Noise-level function of the luma: (bin centres 0..255, noise variance in DN^2).

    Variance of a high-passed image in ``block`` x ``block`` tiles, binned by tile mean; the median
    per bin rejects textured tiles. Same estimator for sim and real, so ratios are meaningful.
    """
    g = (0.299 * rgb_u8[0] + 0.587 * rgb_u8[1] + 0.114 * rgb_u8[2]).float()
    hp, gain = _hp(g)
    H, W = g.shape
    H, W = H // block * block, W // block * block
    mu = F.avg_pool2d(g[None, None, :H, :W], block)[0, 0].reshape(-1)
    var = (F.avg_pool2d((hp[None, None, :H, :W]) ** 2, block)[0, 0].reshape(-1)) / gain
    edges = torch.linspace(0, 255, bins + 1)
    centers, vals = [], []
    for i in range(bins):
        m = (mu >= edges[i]) & (mu < edges[i + 1] + (1 if i == bins - 1 else 0))
        if int(m.sum()) >= 20:
            centers.append(float(mu[m].mean()))
            vals.append(float(var[m].median()))
    return np.array(centers), np.array(vals)


def nps_ratio(sim_u8, real_u8, bands: int = 8, size: int = 256):
    """Radially averaged noise power spectrum of the centre crop: (sim, real, ratio) per band."""
    def spec(x):
        g = (0.299 * x[0] + 0.587 * x[1] + 0.114 * x[2]).float()
        H, W = g.shape
        s = min(size, H, W)
        y0, x0 = (H - s) // 2, (W - s) // 2
        c = g[y0:y0 + s, x0:x0 + s]
        hp, _ = _hp(c, 3.0)
        hp = hp - hp.mean()
        win = torch.hann_window(s, periodic=False)
        p = torch.fft.fftshift(torch.fft.fft2(hp * (win[:, None] * win[None, :])).abs() ** 2)
        yy, xx = torch.meshgrid(torch.arange(s), torch.arange(s), indexing="ij")
        rr = torch.sqrt((yy - s // 2) ** 2 + (xx - s // 2) ** 2) / (s // 2)
        out = []
        for b in range(bands):
            m = (rr >= b / bands) & (rr < (b + 1) / bands)
            out.append(float(p[m].mean()))
        return np.array(out)
    a, b = spec(sim_u8), spec(real_u8)
    ratio = np.where(b > 1e-6, a / np.maximum(b, 1e-12), np.nan)      # flat / saturated frames: undefined
    return a, b, ratio


# ----------------------------------------------------------------------------------------------
# Neural3A reward terms
# ----------------------------------------------------------------------------------------------
def center_crop(x: torch.Tensor, size: int = CROP) -> torch.Tensor:
    H, W = x.shape[-2:]
    s = min(size, H, W)
    y0, x0 = (H - s) // 2, (W - s) // 2
    return x[..., y0:y0 + s, x0:x0 + s]


def luma01(rgb_u8: torch.Tensor) -> torch.Tensor:
    r, g, b = (rgb_u8.float() / 255.0)
    return 0.299 * r + 0.587 * g + 0.114 * b


def midtone_brightness(y: torch.Tensor) -> torch.Tensor:
    return 1 - torch.mean(torch.abs(y - 0.5), dim=(-2, -1))


def sobel_mean(y: torch.Tensor) -> torch.Tensor:
    x = y[None, None].float()
    sx = torch.tensor([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=x.dtype).view(1, 1, 3, 3)
    sy = torch.tensor([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=x.dtype).view(1, 1, 3, 3)
    return torch.sqrt(F.conv2d(x, sx) ** 2 + F.conv2d(x, sy) ** 2).mean()


def _sobel9():
    try:
        import cv2
        kx, ky = cv2.getDerivKernels(1, 0, ksize=9)
        gx = np.outer(ky.flatten(), kx.flatten())
        kx, ky = cv2.getDerivKernels(0, 1, ksize=9)
        gy = np.outer(ky.flatten(), kx.flatten())
    except ImportError:
        d = np.array([1, 6, 14, 14, 0, -14, -14, -6, -1], float)
        s = np.array([1, 8, 28, 56, 70, 56, 28, 8, 1], float)
        gx, gy = np.outer(s, d), np.outer(d, s)
    return (torch.tensor(gx, dtype=torch.float32).view(1, 1, 9, 9), torch.tensor(gy, dtype=torch.float32).view(1, 1, 9, 9))


def tenengrad_mean(y: torch.Tensor) -> torch.Tensor:
    x = y[None, None].float()
    kx, ky = _sobel9()
    return (F.conv2d(x, kx) ** 2 + F.conv2d(x, ky) ** 2).mean() / 100_000


def reward_terms(rgb_u8: torch.Tensor, crop: int = CROP) -> Dict[str, float]:
    """Neural3A terms on the centre crop of an RGB uint8 (3,H,W) frame."""
    y = luma01(center_crop(rgb_u8, crop))
    m = float(midtone_brightness(y))
    s_raw = float(sobel_mean(y))
    t_raw = float(tenengrad_mean(y))
    return {"midtone": m, "sobel_raw": s_raw, "sobel": 2 * s_raw / (s_raw + KS),
            "tenengrad_raw": t_raw, "tenengrad": 2 * t_raw / (t_raw + KT)}


def rel_err(a: float, b: float, eps: float = 1e-9) -> float:
    return abs(a - b) / max(abs(b), eps)
