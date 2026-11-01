"""Validation of simulated frames against the real sweep (and raw-domain validation)."""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import torch

from . import metrics as M
from .io import sidecar as sc
from .io.dng import load_dng
from .simulator import CameraSimulator


@dataclass
class Thresholds:
    ev_abs: float = 0.05            # |mean EV error| of the noise-free render
    psnr_gap_db: float = 1.0        # PSNR within this many dB of the real-vs-real noise floor
    reward_rel: float = 0.05        # relative error of each Neural3A reward term

    @classmethod
    def load(cls, path=None):
        if path is None:
            return cls()
        return cls(**json.loads(Path(path).read_text()))


def _t(u8: np.ndarray) -> torch.Tensor:
    return torch.from_numpy(np.ascontiguousarray(u8)).permute(2, 0, 1).contiguous()


def _nanmean(x):
    x = np.asarray(x, float)
    return float(np.nanmean(x)) if np.isfinite(x).any() else float("nan")


def validate_png(sim: CameraSimulator, out_dir, K: int = 4, seed: int = 0, thresholds: Optional[Thresholds] = None,
                 raw_dir=None, max_points: Optional[int] = None, progress: Optional[Callable[[str], None]] = print,
                 crop: int = M.CROP, chunk: Optional[int] = None) -> dict:
    """Compare sim vs real for every sweep capture at the raw frame's focus within the ranges."""
    th = thresholds or Thresholds()
    pts = sim.grid_points()
    if max_points:
        pts = pts[:max_points]
    if not pts:
        raise ValueError("no sweep captures at the raw frame's focus within the supported ISO/exposure range")
    # noise floor: repeated real PNGs from the capture test
    reps: Dict[tuple, List[Path]] = {}
    if raw_dir is not None:
        man = sc.CaptureManifest.load(raw_dir)
        for e in man.by_tag("rep"):
            if e.png and man.path(e.png).exists() and e.iso and e.exposure_s:
                reps.setdefault((round(e.iso), round(e.exposure_s * 1e6)), []).append(man.path(e.png))
    rows, extras, crops = [], {}, {}
    ds = sim.sweep.downscale()
    isos_u = sorted({c.iso for c in pts})
    exps_u = sorted({c.exposure_s for c in pts})
    pick = lambda vals: sorted({vals[0], vals[len(vals) // 2], vals[-1]})
    show = {(i, e) for i in pick(isos_u) for e in pick(exps_u)}

    t_start = time.time()
    for n, c in enumerate(pts):
        real = _t(sim.sweep.load_png(c))
        iso, t = c.iso, min(c.exposure_s, sim.scene.t0)
        gains = c.gains or sim.wb_gains
        wbs = [gains] * (1 + K)
        out = sim.render_batch([iso] * (1 + K), [t] * (1 + K), wbs, noisy=True, seed=seed + n, chunk=chunk, keep_raw=False)
        # element 0 is rendered noisy too; render the noise-free mean separately
        mean = sim.render_batch([iso], [t], [gains], noisy=False, seed=seed, chunk=1, keep_raw=False)
        mean_png, noisy = mean["png"][0], out["png"][1:]
        if tuple(mean_png.shape) != tuple(real.shape):
            raise ValueError(f"sim PNG {tuple(mean_png.shape)} != real {tuple(real.shape)} for {c.filename}; "
                             "check alignment / downscale")
        valid = ~mean["png_clip_mask"][0]
        row = {"index": c.index, "filename": c.filename, "iso": iso, "exposure_s": c.exposure_s,
               "log2_iso": math.log2(iso), "log2_exposure": math.log2(c.exposure_s),
               "valid_fraction": float(valid.float().mean())}
        H, W = real.shape[-2:]
        for scope, sl in (("full", (slice(None), slice(None))),
                          ("crop", (slice(max((H - crop) // 2, 0), max((H - crop) // 2, 0) + crop),
                                    slice(max((W - crop) // 2, 0), max((W - crop) // 2, 0) + crop)))):
            r_, m_, v_ = real[:, sl[0], sl[1]], mean_png[:, sl[0], sl[1]], valid[sl[0], sl[1]]
            if v_.sum() < 100:
                v_ = torch.ones_like(v_)
            ev = M.ev_error(m_, r_, v_)
            dn = M.dn_error(m_, r_, v_)
            emd = M.emd_per_channel(m_, r_, v_)
            row.update({f"ev_mean_{scope}": _nanmean(ev["mean"]), f"ev_median_{scope}": _nanmean(ev["median"]),
                        f"dn_mean_{scope}": _nanmean(dn["mean"]), f"emd_{scope}": _nanmean(emd)})
            for ch, name in enumerate("rgb"):
                row[f"ev_mean_{name}_{scope}"] = ev["mean"][ch]
            ps, ss, ma = [], [], []
            for k in range(K):
                s_ = noisy[k][:, sl[0], sl[1]]
                ps.append(M.psnr(s_, r_, v_)); ss.append(M.ssim(s_, r_, v_)); ma.append(M.mae(s_, r_, v_))
            row.update({f"psnr_{scope}": _nanmean(ps), f"ssim_{scope}": _nanmean(ss), f"mae_{scope}": _nanmean(ma),
                        f"psnr_mean_render_{scope}": M.psnr(m_, r_, v_)})
        key = (round(iso), round(c.exposure_s * 1e6))
        floor = None
        if key in reps:
            other = _t(__import__("camsim.io.image", fromlist=["read_image_rgb"]).read_image_rgb(reps[key][0]))
            if other.shape == real.shape:
                floor = M.psnr(other, real, valid)
        row["psnr_noise_floor_full"] = floor if floor is not None else float("nan")
        # noise
        cn, vn = M.noise_level_function(noisy[0])
        cr, vr = M.noise_level_function(real)
        common = min(len(cn), len(cr))
        if common:
            xs = np.linspace(0, 255, 12)
            a = np.interp(xs, cn, vn) if len(cn) else np.nan
            b = np.interp(xs, cr, vr) if len(cr) else np.nan
            sel = (xs > cn.min() if len(cn) else xs > 0) & (xs < cn.max() if len(cn) else xs > 0) & (xs > cr.min()) & (xs < cr.max())
            row["nlf_ratio"] = float(np.exp(np.mean(np.log(np.maximum(a[sel], 1e-6) / np.maximum(b[sel], 1e-6))))) if sel.any() else float("nan")
        else:
            row["nlf_ratio"] = float("nan")
        _, _, nr = M.nps_ratio(noisy[0], real)
        row["nps_ratio_mid"] = float(np.exp(np.nanmean(np.log(np.maximum(nr[1:6], 1e-9))))) if np.isfinite(nr[1:6]).any() else float("nan")
        # Neural3A rewards
        rr = M.reward_terms(real, crop)
        rs = [M.reward_terms(noisy[k], crop) for k in range(K)]
        rm = M.reward_terms(mean_png, crop)
        for term in ("midtone", "sobel", "tenengrad"):
            row[f"reward_{term}_real"] = rr[term]
            row[f"reward_{term}_sim"] = float(np.mean([x[term] for x in rs]))
            row[f"reward_{term}_rel_err"] = M.rel_err(row[f"reward_{term}_sim"], rr[term])
            row[f"reward_{term}_rel_err_mean_render"] = M.rel_err(rm[term], rr[term])
        rows.append(row)
        if (iso, c.exposure_s) in show:
            y0, x0 = (H - 128) // 2, (W - 128) // 2
            crops[(iso, c.exposure_s)] = (real[:, y0:y0 + 128, x0:x0 + 128].clone(),
                                          noisy[0][:, y0:y0 + 128, x0:x0 + 128].clone())
        if (iso, c.exposure_s) in show:
            extras[(iso, c.exposure_s)] = {"nlf_sim": (cn.tolist(), vn.tolist()), "nlf_real": (cr.tolist(), vr.tolist())}
        if progress and (n % 10 == 0 or n == len(pts) - 1):
            progress(f"  validated {n + 1}/{len(pts)} ({time.time() - t_start:.0f}s)")

    summary = summarise(rows, th)
    result = {"rows": rows, "summary": summary, "thresholds": asdict(th), "extras": extras, "crops": crops,
              "K": K, "downscale": ds, "focus_d": sim.focus_d, "notes": sim.notes}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "metrics.csv", rows)
    (out_dir / "summary.json").write_text(json.dumps({"summary": summary, "thresholds": asdict(th), "K": K,
                                                      "focus_d": sim.focus_d, "notes": sim.notes}, indent=2))
    return result


def write_csv(path, rows):
    import csv
    if not rows:
        return
    keys = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


MIN_VALID_FRACTION = 0.2


def summarise(rows_all, th: Thresholds) -> dict:
    """Aggregate and apply the (reported, not enforced) thresholds.

    Grid points where more than 80 % of the frame is clipped in the simulation are listed in
    ``excluded`` and left out of the criteria: nothing meaningful can be compared there.
    """
    rows = [r for r in rows_all if r["valid_fraction"] >= MIN_VALID_FRACTION] or rows_all
    ev = np.array([r["ev_mean_full"] for r in rows])
    gap = np.array([r["psnr_noise_floor_full"] - r["psnr_full"] for r in rows])
    out = {"n_points": len(rows_all), "n_evaluated": len(rows),
           "excluded": [{"iso": r["iso"], "exposure_s": r["exposure_s"], "valid_fraction": r["valid_fraction"]}
                        for r in rows_all if r not in rows],
           "ev_error_full": {"mean_abs": float(np.nanmean(np.abs(ev))), "max_abs": float(np.nanmax(np.abs(ev))),
                             "bias": float(np.nanmean(ev))},
           "psnr_full_mean": float(np.nanmean([r["psnr_full"] for r in rows])),
           "ssim_full_mean": float(np.nanmean([r["ssim_full"] for r in rows])),
           "criteria": {}}
    out["criteria"]["ev"] = {"threshold": th.ev_abs, "pass_fraction": float(np.nanmean(np.abs(ev) <= th.ev_abs)),
                             "pass": bool(np.nanmax(np.abs(ev)) <= th.ev_abs)}
    if np.isfinite(gap).any():
        out["criteria"]["psnr_vs_floor"] = {"threshold_db": th.psnr_gap_db,
                                            "pass_fraction": float(np.nanmean(gap[np.isfinite(gap)] <= th.psnr_gap_db)),
                                            "pass": bool(np.nanmax(gap) <= th.psnr_gap_db), "n": int(np.isfinite(gap).sum())}
    else:
        out["criteria"]["psnr_vs_floor"] = {"threshold_db": th.psnr_gap_db, "pass": None,
                                            "note": "no repeated real frames (noise floor) available"}
    for term in ("midtone", "sobel", "tenengrad"):
        e = np.array([r[f"reward_{term}_rel_err"] for r in rows])
        out["criteria"][f"reward_{term}"] = {"threshold": th.reward_rel, "mean_rel_err": float(np.nanmean(e)),
                                             "pass_fraction": float(np.nanmean(e <= th.reward_rel)),
                                             "pass": bool(np.nanmax(e) <= th.reward_rel)}
    return out


# ----------------------------------------------------------------------------------------------
# raw-domain validation
# ----------------------------------------------------------------------------------------------
def _plane_curve(raw: np.ndarray, black_2x2, bins=14, block=8, white=1023.0):
    """(mean DN above black, noise variance DN^2) per brightness bin from the CFA planes."""
    means, vars_ = [], []
    for i in range(2):
        for j in range(2):
            x = raw[i::2, j::2].astype(np.float64) - black_2x2[i, j]
            p = np.pad(x, 1, mode="reflect")
            nb = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4
            r2 = (x - nb) ** 2 / 1.25
            H, W = (x.shape[0] // block) * block, (x.shape[1] // block) * block
            mb = x[:H, :W].reshape(H // block, block, W // block, block).mean((1, 3)).ravel()
            vb = r2[:H, :W].reshape(H // block, block, W // block, block).mean((1, 3)).ravel()
            means.append(mb); vars_.append(vb)
    m, v = np.concatenate(means), np.concatenate(vars_)
    edges = np.linspace(0, 0.9 * (white - float(np.mean(black_2x2))), bins + 1)
    cs, vs = [], []
    for k in range(bins):
        s = (m >= edges[k]) & (m < edges[k + 1])
        if s.sum() >= 30:
            cs.append(float(m[s].mean())); vs.append(float(np.median(v[s])))
    return np.array(cs), np.array(vs)


def validate_raw(sim: CameraSimulator, raw_dir, K: int = 2, seed: int = 0, progress=print) -> Optional[dict]:
    """Simulated raw vs real raw for the capture test's validation sub-grid (tag ``val``)."""
    man = sc.CaptureManifest.load(raw_dir)
    ents = [e for e in man.by_tag("val") if e.file.lower().endswith(".dng")]
    if not ents:
        return None
    rows = []
    for e in ents:
        fr = load_dng(man.path(e.file), sidecar=man.sidecar(e))
        iso, t = fr.iso, min(fr.exposure_s, sim.scene.t0)
        if not (50 <= iso <= 1600):
            continue
        out = sim.raw_sim.render_all([iso] * (K + 1), [t] * (K + 1), noisy=True, seed=seed)[0].cpu().numpy()
        mean = sim.raw_sim.render_all([iso], [t], noisy=False)[0][0].cpu().numpy()
        blk = fr.black_2x2
        valid = (fr.bayer < fr.white_level - 2) & (mean < fr.white_level - 2)
        row = {"file": e.file, "iso": iso, "exposure_s": fr.exposure_s}
        blkmap = fr.black_map()
        for k, ch in enumerate(fr.cfa):
            i, j = k // 2, k % 2
            v = valid[i::2, j::2]
            a = (mean[i::2, j::2] - blkmap[i::2, j::2])[v].mean()
            b = (fr.bayer[i::2, j::2] - blkmap[i::2, j::2])[v].mean()
            row[f"ev_err_pos{k}_{ch}"] = float(math.log2(max(a, 1e-3) / max(b, 1e-3)))
        row["ev_err_mean"] = float(np.mean([row[f"ev_err_pos{k}_{ch}"] for k, ch in enumerate(fr.cfa)]))
        cs, vs = _plane_curve(out[1], blk, white=fr.white_level)
        cr, vr = _plane_curve(fr.bayer, blk, white=fr.white_level)
        row["curve_sim"] = (cs.tolist(), vs.tolist())
        row["curve_real"] = (cr.tolist(), vr.tolist())
        if len(cs) and len(cr):
            lo = max(cs.min(), cr.min()); hi = min(cs.max(), cr.max())
            xs = np.linspace(lo, hi, 8) if hi > lo else np.array([lo])
            ratio = np.interp(xs, cs, vs) / np.maximum(np.interp(xs, cr, vr), 1e-6)
            row["noise_var_ratio"] = float(np.exp(np.mean(np.log(np.maximum(ratio, 1e-6)))))
            row["read_floor_sim"], row["read_floor_real"] = float(vs[0]), float(vr[0])
        rows.append(row)
        if progress:
            progress(f"  raw validated {e.file}")
    return {"rows": rows}
