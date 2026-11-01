"""Fit ISP stages from paired (DNG, PNG) captures of the SAME request.

Never call this with evaluation-sweep frames: the pairs come from the capture test (``pairs``
mode on other scenes, plus the scene's own ISO50/100 ms pair, which is part of the raw input).

Procedure: (1) estimate the geometric alignment, (2) fit one shared monotone tone LUT on the
white-balanced camera RGB after the (initial) CCM, (3) refine the CCM by least squares in the
tone-inverted domain (two alternations), (4) optional per-channel LUT, (5) pick the chroma
subsampling method (full-resolution pairs only), (6) report per-stage fit quality and whether the
tone curve depends on ISO / exposure / scene (which would call for ``isp/residual.py``).
"""
from __future__ import annotations

import copy
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn.functional as F

from ..align import Alignment, estimate_alignment, residual_shift
from ..io import sidecar as sc
from ..io.dng import RawFrame
from ..io.image import read_image_rgb
from .pipeline import LUT_SIZE, ISPModel, ISPParams, srgb_oetf_lut


@dataclass
class Pair:
    frame: RawFrame
    png: np.ndarray               # (h, w, 3) uint8
    downscale: int = 1            # png is the sensor grid downscaled by this factor
    wb: Optional[Tuple[float, ...]] = None
    name: str = ""

    @property
    def full_hw(self):
        return self.png.shape[0] * self.downscale, self.png.shape[1] * self.downscale


def load_pair(raw_dir, frame: RawFrame, prefer_full: bool = True) -> Optional[Pair]:
    """The PNG paired with ``frame`` according to ``capture_manifest.json`` (full-res preferred)."""
    if not frame.path:
        return None
    man = sc.CaptureManifest.load(raw_dir)
    name = Path(frame.path).name
    ent = next((e for e in man.entries if e.file == name), None)
    if ent is None:
        return None
    cands = []
    if ent.png_full:
        cands.append((ent.png_full, 1))
    if ent.png:
        cands.append((ent.png, ent.downscale or 1))
    if not prefer_full:
        cands.reverse()
    for fn, ds in cands:
        p = man.path(fn)
        if p.exists():
            return Pair(frame, read_image_rgb(p), ds, frame.wb_gains, name)
    return None


# ----------------------------------------------------------------------------------------------
def pav_increasing(y: np.ndarray, w: Optional[np.ndarray] = None) -> np.ndarray:
    """Pool-adjacent-violators isotonic (non-decreasing) regression."""
    y = np.asarray(y, float)
    w = np.ones_like(y) if w is None else np.asarray(w, float)
    vals, wts, cnt = [], [], []
    for yi, wi in zip(y, w):
        vals.append(yi); wts.append(wi); cnt.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v = (vals[-2] * wts[-2] + vals[-1] * wts[-1]) / (wts[-2] + wts[-1])
            ww, cc = wts[-2] + wts[-1], cnt[-2] + cnt[-1]
            vals[-2:], wts[-2:], cnt[-2:] = [v], [ww], [cc]
    return np.repeat(vals, cnt)


def fit_lut(lin: np.ndarray, target: np.ndarray, n_bins: int = 256, n_out: int = LUT_SIZE) -> np.ndarray:
    """Monotone LUT over u = sqrt(linear) from samples. ``lin`` in [0,1], ``target`` in 0..255."""
    u = np.sqrt(np.clip(lin, 0, 1))
    edges = np.linspace(0, 1, n_bins + 1)
    ib = np.clip(np.digitize(u, edges) - 1, 0, n_bins - 1)
    centers = np.full(n_bins, np.nan)
    med = np.full(n_bins, np.nan)
    cnt = np.bincount(ib, minlength=n_bins)
    order = np.argsort(ib, kind="stable")
    ts, us = target[order], u[order]
    start = 0
    for b in range(n_bins):
        stop = start + cnt[b]
        if cnt[b] >= 8:
            med[b] = np.median(ts[start:stop])
            centers[b] = np.median(us[start:stop])
        start = stop
    ok = ~np.isnan(med)
    if ok.sum() < 4:
        raise ValueError("too few valid samples to fit a tone curve")
    iso = pav_increasing(med[ok], cnt[ok].astype(float))
    xs = np.linspace(0, 1, n_out)
    # linear extrapolation is avoided: clamp to the end values
    return np.clip(np.interp(xs, centers[ok], iso), 0, 255)


def _inv_lut(lut1: np.ndarray, vals: np.ndarray) -> np.ndarray:
    """Invert a monotone 1-D LUT: values (0..255) -> linear."""
    n = len(lut1)
    xs = np.linspace(0, 1, n)
    mono = np.maximum.accumulate(lut1 + 1e-6 * np.arange(n))
    u = np.interp(vals, mono, xs)
    return u ** 2


def _sample_pair(isp: ISPModel, pair: Pair, n: int, rng, device, margin=24):
    """Return cam-RGB (N,3) on the PNG grid, and target (N,3), sampled where neither is clipped."""
    ds = pair.downscale
    raw = torch.as_tensor(pair.frame.bayer, device=device)[None]
    wb = pair.wb or pair.frame.wb_gains or (1, 1, 1, 1)
    full_hw = pair.full_hw
    cam = isp.alignment.warp(isp.cam_rgb(raw, np.asarray(wb, np.float32)[None]), full_hw)
    if ds > 1:
        cam = F.avg_pool2d(cam, ds)
    cam = cam[0].permute(1, 2, 0).cpu().numpy()
    tgt = pair.png.astype(np.float32)
    h, w, _ = tgt.shape
    ys = rng.integers(margin, h - margin, n * 2)
    xs = rng.integers(margin, w - margin, n * 2)
    c = cam[ys, xs]
    t = tgt[ys, xs]
    sat = pair.frame.white_level - 0.01 * pair.frame.range_dn
    ok = (t.max(1) < 254.5) & (t.min(1) > 0.5) & (c.max(1) < 0.98 * 3.0)
    return c[ok][:n], t[ok][:n]


def _rmse(a, b):
    return float(np.sqrt(np.mean((a - b) ** 2)))


def fit_isp_from_pairs(pairs: Sequence[Pair], base: ISPParams, device="cpu", alignment: Optional[Alignment] = None,
                       n_samples: int = 200_000, estimate_align: bool = True, per_channel: Optional[bool] = None,
                       refine_ccm: Optional[bool] = None, ccm_iters: int = 2, seed: int = 0):
    """Fit tone LUT + CCM (+ alignment, chroma method). Returns ``(ISPParams, Alignment, report)``."""
    rng = np.random.default_rng(seed)
    params = copy.deepcopy(base)
    report: Dict = {"n_pairs": len(pairs), "stages": {}}
    isp = ISPModel(params, device, alignment)

    # ---- (1) alignment, estimated on the first pair with the current (default) ISP
    p0 = pairs[0]
    align = alignment or Alignment()
    if estimate_align and alignment is None:
        raw = torch.as_tensor(p0.frame.bayer, device=device)[None]
        wb = np.asarray(p0.wb or p0.frame.wb_gains or (1, 1, 1, 1), np.float32)[None]
        cam = isp.cam_rgb(raw, wb)
        lin = isp.apply_ccm(cam)
        g = isp.tone(lin)[0].mean(0).cpu().numpy() / 255.0
        ds = p0.downscale
        ref = p0.png.astype(np.float32).mean(-1) / 255.0
        sim_ds = F.avg_pool2d(torch.from_numpy(g)[None, None], ds)[0, 0].numpy() if ds > 1 else g
        al = estimate_alignment(sim_ds, ref)
        if ds > 1:
            C = np.array([[1 / ds, 0, 0.5 / ds - 0.5], [0, 1 / ds, 0.5 / ds - 0.5], [0, 0, 1]])
            al = Alignment(np.linalg.inv(C) @ al.matrix @ C, p0.full_hw, al.residual_px)
        align = al
        report["stages"]["alignment"] = {"matrix": align.matrix.tolist(), "residual_px_after": align.residual_px}
    elif align.out_hw is None:
        align = Alignment(align.matrix, p0.full_hw, align.residual_px)
    isp = ISPModel(params, device, align)

    # ---- (2,3) samples + alternating tone / CCM fit
    cams, tgts, ids = [], [], []
    per = max(n_samples // len(pairs), 20_000)
    for k, pr in enumerate(pairs):
        c, t = _sample_pair(isp, pr, per, rng, device)
        cams.append(c); tgts.append(t); ids.append(np.full(len(c), k))
    cam, tgt, pid = np.concatenate(cams), np.concatenate(tgts), np.concatenate(ids)
    hold = rng.random(len(cam)) < 0.2

    def apply(camv, ccm_, lut_):
        l = torch.as_tensor(np.clip(camv @ ccm_.T, 0, 1), dtype=torch.float32)
        t_lut = torch.as_tensor(lut_, dtype=torch.float32)
        u = l.sqrt() * (LUT_SIZE - 1)
        i0 = u.floor().clamp(0, LUT_SIZE - 2).long()
        f = u - i0
        return torch.stack([t_lut[c][i0[:, c]] * (1 - f[:, c]) + t_lut[c][i0[:, c] + 1] * f[:, c] for c in range(3)], 1).numpy()

    def shared_lut(ccm_):
        lin_ = np.clip(cam[~hold] @ ccm_.T, 0, 1)
        return np.tile(fit_lut(lin_.reshape(-1), tgt[~hold].reshape(-1))[None], (3, 1))

    ccm0 = np.asarray(params.ccm, float).copy()
    ccm = ccm0.copy()
    lut3 = shared_lut(ccm)
    base_rmse = _rmse(apply(cam[hold], ccm, lut3), tgt[hold])
    # CCM refinement is only trustworthy with enough colour diversity (>= 3 pairs); the HAL / DNG
    # matrix is otherwise kept. A refined CCM is also dropped if it does not improve the hold-out error.
    if refine_ccm is None:
        refine_ccm = len(pairs) >= 3
    report["stages"]["ccm_refinement"] = {"requested": bool(refine_ccm), "pairs": len(pairs)}
    if refine_ccm:
        for it in range(ccm_iters):
            shared = lut3[0]
            mid = (tgt[~hold] > 6) & (tgt[~hold] < 248)
            inv = np.stack([_inv_lut(shared, tgt[~hold][:, c]) for c in range(3)], 1)
            w = mid.all(1)
            A, b = cam[~hold][w], inv[w]
            lam = 1e-3 * len(A)
            new = np.linalg.solve(A.T @ A + lam * np.eye(3), A.T @ b + lam * ccm.T).T   # ridge toward current
            if abs(ccm.sum(1) - 1).max() < 0.05:
                new = new / new.sum(1, keepdims=True)      # preserve neutrals
            ccm = new
            lut3 = shared_lut(ccm)
        ref_rmse = _rmse(apply(cam[hold], ccm, lut3), tgt[hold])
        report["stages"]["ccm_refinement"].update({"rmse_before": base_rmse, "rmse_after": ref_rmse})
        if ref_rmse >= base_rmse:
            ccm, lut3 = ccm0, shared_lut(ccm0)
            report["stages"]["ccm_refinement"]["kept"] = "initial"
    params.ccm = ccm

    if per_channel is None:
        per_channel = len(pairs) >= 3
    if per_channel:
        lin = np.clip(cam[~hold] @ ccm.T, 0, 1)
        lut3 = np.stack([fit_lut(lin[:, c], tgt[~hold][:, c]) for c in range(3)])
    params.tone_lut = lut3
    params.source = "fitted"

    # ---- evaluation on held-out samples
    pred = apply(cam[hold], ccm, lut3)
    report["stages"]["tone_ccm"] = {"holdout_rmse_dn_rgb": [_rmse(pred[:, c], tgt[hold][:, c]) for c in range(3)],
                                    "holdout_rmse_dn": _rmse(pred, tgt[hold]), "ccm": ccm.tolist(),
                                    "per_channel_lut": bool(per_channel)}

    # ---- does the tone curve depend on ISO / exposure / scene?
    pooled = fit_lut(np.clip(cam @ ccm.T, 0, 1).reshape(-1), tgt.reshape(-1))
    xs = np.array([0.04, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9])
    gi = (xs * (LUT_SIZE - 1)).astype(int)
    devs = []
    for k, pr in enumerate(pairs):
        m = pid == k
        if m.sum() < 5000:
            continue
        try:
            lk = fit_lut(np.clip(cam[m] @ ccm.T, 0, 1).reshape(-1), tgt[m].reshape(-1))
        except ValueError:
            continue
        d = lk[gi] - pooled[gi]
        devs.append({"pair": pr.name, "iso": pr.frame.iso, "exposure_s": pr.frame.exposure_s,
                     "max_abs_dev_dn": float(np.abs(d).max()), "dev_dn": d.tolist()})
    report["tone_dependence"] = {"per_pair": devs, "max_dev_dn": max([d["max_abs_dev_dn"] for d in devs], default=None)}
    md = report["tone_dependence"]["max_dev_dn"]
    report["tone_varies"] = bool(md is not None and md > 3.0)
    if report["tone_varies"]:
        report["recommendation"] = ("Per-pair tone curves differ by > 3 DN: the ISP is likely adaptive "
                                    "(exposure/ISO/scene dependent tone mapping). Train isp/residual.py with "
                                    "leave-one-scene-out validation (fit_calibration.py --residual).")

    # ---- chroma method (needs a full-resolution pair)
    full = [p for p in pairs if p.downscale == 1]
    if full:
        errs = {}
        pr = full[0]
        raw = torch.as_tensor(pr.frame.bayer, device=device)[None]
        wb = np.asarray(pr.wb or pr.frame.wb_gains or (1, 1, 1, 1), np.float32)[None]
        isp2 = ISPModel(params, device, align)
        g = isp2.tone(isp2.linear_rgb(raw, wb, pr.full_hw))
        tg = torch.from_numpy(pr.png.astype(np.float32)).permute(2, 0, 1)[None].to(device)
        mid = (tg > 4) & (tg < 251)
        for m in ("average", "topleft"):
            p2 = copy.deepcopy(params); p2.chroma = m
            out = ISPModel(p2, device, align).encode_decode(g).float()
            errs[m] = float(torch.sqrt(((out - tg)[mid] ** 2).mean()))
        params.chroma = min(errs, key=errs.get)
        report["stages"]["chroma"] = {"rmse_by_method": errs, "chosen": params.chroma}
    else:
        report["stages"]["chroma"] = {"note": "no full-resolution pair; chroma method left at 'average'"}

    report["summary"] = (f"hold-out RMSE {report['stages']['tone_ccm']['holdout_rmse_dn']:.2f} DN"
                         + (f", tone varies across pairs (max dev {md:.1f} DN)" if report["tone_varies"] else ""))
    return params, align, report
