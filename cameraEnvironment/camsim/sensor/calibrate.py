"""Sensor calibration from raw captures (Linux side).

* Photon-transfer curve (PTC) by the difference method: for two frames A, B of the same flat
  field and settings, ``var(A-B)/2`` vs ``mean`` per bin has slope = gain g (DN/e) and intercept =
  read-noise variance (DN^2, includes the 1/12 quantisation term). Differencing removes PRNU and
  fixed-pattern noise.
* Gain law over ISO (not assumed proportional to ISO) and detection of the analog -> digital gain
  boundary from histogram gaps (a digital gain G > 1 on integers leaves empty codes).
* Dark frames: read noise per ISO, row noise, dark current vs exposure, DSNU / hot-pixel map.
* Flat frames: lens-shading gain map.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .model import SensorParams


class PTCAccumulator:
    """Streaming mean-variance accumulator over (A, B) frame pairs."""

    def __init__(self, n_bins: int = 48, max_frac: float = 0.85):
        self.n_bins, self.max_frac = n_bins, max_frac
        self.cnt = np.zeros(n_bins)
        self.sum_m = np.zeros(n_bins)
        self.sum_v = np.zeros(n_bins)
        self.range_dn = None

    def add_pair(self, a: np.ndarray, b: np.ndarray, black_2x2: np.ndarray, white: float):
        h, w = a.shape
        blk = np.tile(black_2x2, ((h + 1) // 2, (w + 1) // 2))[:h, :w]
        rng = white - float(np.mean(black_2x2))
        self.range_dn = rng
        m = (a + b) / 2 - blk
        d = (a - b)
        valid = (a < white - 2) & (b < white - 2) & (m > 0)
        # robust: drop outliers of the difference (hot pixels, cosmic events)
        s = 1.4826 * np.median(np.abs(d[valid] - np.median(d[valid]))) + 1e-9
        valid &= np.abs(d) < 6 * s
        edges = np.linspace(0, self.max_frac * rng, self.n_bins + 1)
        idx = np.digitize(m[valid], edges) - 1
        ok = (idx >= 0) & (idx < self.n_bins)
        idx, mm, dd = idx[ok], m[valid][ok], d[valid][ok]
        self.cnt += np.bincount(idx, minlength=self.n_bins)
        self.sum_m += np.bincount(idx, weights=mm, minlength=self.n_bins)
        self.sum_v += np.bincount(idx, weights=dd * dd / 2.0, minlength=self.n_bins)

    def fit(self, min_count: int = 2000) -> dict:
        ok = self.cnt >= min_count
        if ok.sum() < 4:
            raise ValueError("PTC: fewer than 4 populated bins; need a wider exposure ramp")
        m = self.sum_m[ok] / self.cnt[ok]
        v = self.sum_v[ok] / self.cnt[ok]
        w = np.sqrt(self.cnt[ok])
        A = np.stack([m, np.ones_like(m)], 1) * w[:, None]
        (g, r2), *_ = np.linalg.lstsq(A, v * w, rcond=None)
        pred = g * m + r2
        ss_res = float(np.sum(w ** 2 * (v - pred) ** 2))
        ss_tot = float(np.sum(w ** 2 * (v - np.average(v, weights=w ** 2)) ** 2))
        return {"gain_dn_per_e": float(g), "read_var_dn2": float(r2),
                "read_dn": float(math.sqrt(max(r2, 0.0))), "n_bins": int(ok.sum()),
                "r2": 1 - ss_res / max(ss_tot, 1e-12), "mean_dn": m.tolist(), "var_dn2": v.tolist()}


def detect_gain_gaps(frame_dn: np.ndarray, lo: float = 0.15, hi: float = 0.6, white: float = 1023.0,
                     black: float = 0.0) -> dict:
    """Histogram occupancy of integer codes in a mid-range region.

    With purely analog gain and noise of several DN, every code in the range is populated
    (occupancy ~ 1). A digital gain G > 1 applied to integers leaves ~(1 - 1/G) of the codes empty.
    Returns ``occupancy`` and the implied digital gain ``1 / occupancy``.
    """
    x = np.rint(frame_dn - black).astype(np.int64)
    sel = x[(x > lo * (white - black)) & (x < hi * (white - black))]
    if sel.size < 10000:
        return {"occupancy": float("nan"), "implied_digital_gain": float("nan")}
    h = np.bincount(sel - sel.min())
    # use the central 90 % of codes to avoid sparse tails
    cdf = np.cumsum(h) / h.sum()
    a, b = np.searchsorted(cdf, 0.05), np.searchsorted(cdf, 0.95)
    core = h[a:b + 1]
    occ = float((core > 0).mean())
    return {"occupancy": occ, "implied_digital_gain": float(1.0 / max(occ, 1e-3))}


def fit_gain_law(per_iso: Dict[float, dict], occupancy: Optional[Dict[float, float]] = None,
                 gap_threshold: float = 0.85) -> dict:
    """From per-ISO PTC fits build ``gain_table``, check proportionality and find the analog limit."""
    isos = sorted(per_iso)
    table = [(float(i), per_iso[i]["gain_dn_per_e"]) for i in isos]
    ratio = [g / i for i, g in table]
    out = {"gain_table": table, "gain_per_iso": ratio,
           "max_deviation_from_proportional": float(max(ratio) / min(ratio) - 1) if len(ratio) > 1 else 0.0,
           "analog_iso_max": None}
    if occupancy:
        occ_sorted = sorted((i, o) for i, o in occupancy.items() if not np.isnan(o))
        digital = [i for i, o in occ_sorted if o < gap_threshold]
        if digital:
            analog = [i for i, o in occ_sorted if o >= gap_threshold and i < min(digital)]
            out["analog_iso_max"] = float(max(analog)) if analog else float(min(i for i, _ in occ_sorted))
            out["digital_isos"] = [float(i) for i in digital]
    return out


@dataclass
class DarkStats:
    """Accumulates dark-frame statistics per (iso) and exposure."""
    mean_by_iso_t: Dict[Tuple[float, float], np.ndarray] = field(default_factory=dict)
    count_by_iso_t: Dict[Tuple[float, float], int] = field(default_factory=dict)
    read_dn: Dict[float, List[float]] = field(default_factory=dict)
    row_var: Dict[float, List[float]] = field(default_factory=dict)
    black_2x2: Dict[Tuple[float, float], np.ndarray] = field(default_factory=dict)

    def add_group(self, iso: float, t: float, frames: Sequence[np.ndarray], black_hint: np.ndarray):
        """``frames``: >=2 dark frames at one setting (float, raw DN)."""
        key = (float(iso), float(t))
        mean = np.mean(frames, 0)
        self.mean_by_iso_t[key] = mean.astype(np.float32)
        self.count_by_iso_t[key] = len(frames)
        self.black_2x2[key] = np.array([[mean[i::2, j::2].mean() for j in range(2)] for i in range(2)])
        if len(frames) >= 2:
            d = (frames[0].astype(np.float64) - frames[1]) / math.sqrt(2.0)
            s = []
            for i in range(2):
                for j in range(2):
                    x = d[i::2, j::2].ravel()
                    med = np.median(x)
                    mad = 1.4826 * np.median(np.abs(x - med))
                    # plain std after clipping outliers (hot pixels): the MAD alone is a coarse
                    # estimator on integer data when sigma is only a few DN
                    x = x[np.abs(x - med) < max(8 * mad, 4.0)]
                    s.append(float(np.std(x)))
            sigma = float(np.mean(s))
            self.read_dn.setdefault(key[0], []).append((key[1], sigma))
            rowmean = d.mean(1)
            rv = float(np.var(rowmean) - sigma ** 2 / d.shape[1])
            self.row_var.setdefault(key[0], []).append((key[1], rv))


def fit_dark(stats: DarkStats, gain_fn, hot_sigma: float = 6.0) -> dict:
    """Read noise table, row noise, dark current, DSNU map from accumulated dark statistics."""
    out: dict = {"read_noise_table": [], "row_noise_e": 0.0, "dark_rate_e_s": 0.0, "dsnu_e_s": None, "notes": []}
    for iso, lst in sorted(stats.read_dn.items()):
        # read noise at the shortest exposure (least dark current contribution)
        t, sig = min(lst, key=lambda x: x[0])
        out["read_noise_table"].append((iso, float(sig)))
    rows = []
    for iso, lst in stats.row_var.items():
        t, rv = min(lst, key=lambda x: x[0])
        if rv > 0:
            rows.append(math.sqrt(rv) / gain_fn(iso))
    if rows:
        out["row_noise_e"] = float(np.median(rows))
    # dark current: per-pixel slope of (mean - mean at shortest t) vs t, using the highest-ISO-weighted estimate
    rates = []
    for iso in sorted({k[0] for k in stats.mean_by_iso_t}):
        ts = sorted(t for (i, t) in stats.mean_by_iso_t if i == iso)
        if len(ts) < 2 or ts[-1] - ts[0] < 5e-3:
            continue
        t_lo, t_hi = ts[0], ts[-1]
        d = (stats.mean_by_iso_t[(iso, t_hi)].astype(np.float64) - stats.mean_by_iso_t[(iso, t_lo)]) / (t_hi - t_lo)
        rates.append(d / gain_fn(iso))      # e/s per pixel
    if rates:
        r = np.mean(rates, 0)
        med = float(np.median(r))
        sig = 1.4826 * float(np.median(np.abs(r - med))) + 1e-9
        out["dark_rate_e_s"] = max(med, 0.0)
        hot = np.where(r > med + hot_sigma * sig, r - med, 0.0).astype(np.float32)
        out["dsnu_e_s"] = hot
        out["hot_pixel_count"] = int((hot > 0).sum())
        out["notes"].append(f"dark current median {med:.3g} e/s (noise of estimate {sig:.3g} e/s); "
                            f"{out['hot_pixel_count']} hot pixels > {hot_sigma} sigma kept in the DSNU map")
    else:
        out["notes"].append("no dark-current estimate (needs >=2 exposures spanning >5 ms at one ISO)")
    return out


def fit_shading_from_flat(frames: Sequence[np.ndarray], black_2x2: np.ndarray, white: float,
                          grid: Tuple[int, int] = (13, 17)) -> np.ndarray:
    """Lens-shading gain map (gh, gw, 4), 1 at the brightest point, from flat-field frames."""
    import cv2
    planes = []
    for k in range(4):
        i, j = k // 2, k % 2
        acc = np.mean([f[i::2, j::2] - black_2x2[i, j] for f in frames], 0).astype(np.float32)
        acc = cv2.GaussianBlur(acc, (0, 0), max(acc.shape) / 64)
        planes.append(cv2.resize(acc, (grid[1], grid[0]), interpolation=cv2.INTER_AREA))
    p = np.stack(planes, -1)
    ref = p.max((0, 1), keepdims=True)      # per CFA plane: shading only, not channel balance
    return (ref / np.maximum(p, 1e-3 * ref)).astype(np.float32)
