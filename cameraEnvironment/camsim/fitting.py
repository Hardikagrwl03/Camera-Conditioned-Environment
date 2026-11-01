"""Orchestration of calibration fitting from capture folders (used by scripts/fit_calibration.py)."""
from __future__ import annotations

import warnings
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

from .calibration import Calibration
from .io import sidecar as sc
from .io.dng import RawFrame, load_dng
from .isp.fit import Pair, fit_isp_from_pairs, load_pair
from .isp.pipeline import ISPParams, default_isp_params
from .sensor import calibrate as C
from .sensor.model import SensorModel, SensorParams


def _groups(man: sc.CaptureManifest, tag: str):
    g = defaultdict(list)
    for e in man.by_tag(tag):
        if e.file.lower().endswith(".dng") and e.iso and e.exposure_s:
            g[(float(e.iso), round(e.exposure_s, 7))].append(e)
    return g


def fit_sensor(raw_root, log: Callable[[str], None] = print):
    """Fit SensorParams (+ DSNU map, shading map) from ``<raw_root>/flat`` and ``<raw_root>/dark``.

    Returns ``(SensorParams | None, dsnu | None, shading | None, report)``. Missing folders
    degrade gracefully (the corresponding parts stay at the DNG NoiseProfile fallback).
    """
    raw_root = Path(raw_root)
    report: dict = {"notes": []}
    params = SensorParams(source="calibration")
    shading, dsnu = None, None
    black_hint, white = None, None

    flat = raw_root / "flat"
    if (flat / "capture_manifest.json").exists():
        man = sc.CaptureManifest.load(flat)
        per_iso: Dict[float, C.PTCAccumulator] = {}
        occ: Dict[float, float] = {}
        shade_frames = []
        for (iso, t), ents in sorted(_groups(man, "flat").items()):
            ents = sorted(ents, key=lambda e: e.frame)
            if len(ents) < 2:
                continue
            a = load_dng(man.path(ents[0].file), sidecar=man.sidecar(ents[0]))
            b = load_dng(man.path(ents[1].file), sidecar=man.sidecar(ents[1]))
            black_hint, white = a.black_2x2, a.white_level
            acc = per_iso.setdefault(iso, C.PTCAccumulator())
            acc.add_pair(a.bayer.astype(np.float64), b.bayer.astype(np.float64), a.black_2x2, a.white_level)
            mean = (a.bayer.astype(np.float64) + b.bayer) / 2
            rng = a.range_dn
            level = float(np.mean(mean - a.black_map()))
            if 0.25 * rng < level < 0.6 * rng:
                occ.setdefault(iso, C.detect_gain_gaps(a.bayer, white=a.white_level, black=float(a.black_2x2.mean()))["occupancy"])
                if iso == min(per_iso) and len(shade_frames) < 4:
                    shade_frames.append(a.bayer.astype(np.float32))
            log(f"  flat ISO {iso:g} t={t * 1e3:.3g} ms: mean {level:.0f} DN")
        fits = {}
        for iso, acc in per_iso.items():
            try:
                fits[iso] = acc.fit()
            except ValueError as e:
                report["notes"].append(f"PTC for ISO {iso:g} failed: {e}")
        if fits:
            law = C.fit_gain_law(fits, occ)
            params.iso_ref = min(fits)
            params.gain_table = law["gain_table"]
            params.gain_ref = fits[params.iso_ref]["gain_dn_per_e"]
            params.analog_iso_max = law["analog_iso_max"]
            params.read_noise_table = [(i, f["read_dn"]) for i, f in sorted(fits.items())]
            report["ptc"] = {str(i): {k: v for k, v in f.items() if k not in ("mean_dn", "var_dn2")} for i, f in fits.items()}
            report["ptc_curves"] = {str(i): {"mean_dn": f["mean_dn"], "var_dn2": f["var_dn2"]} for i, f in fits.items()}
            report["gain_law"] = {k: v for k, v in law.items()}
            report["occupancy"] = {str(k): v for k, v in occ.items()}
            if 50 in fits and 100 in fits:
                report["iso50_vs_iso100_gain_ratio"] = fits[50]["gain_dn_per_e"] / fits[100]["gain_dn_per_e"]
            log(f"  gain law: max deviation from proportional-to-ISO {100 * law['max_deviation_from_proportional']:.1f} %, "
                f"analog ISO max {law['analog_iso_max']}")
        if shade_frames and black_hint is not None:
            shading = C.fit_shading_from_flat(shade_frames, black_hint, white)
    else:
        report["notes"].append(f"no flat captures in {flat}: gain/read noise left to the DNG NoiseProfile")

    dark = raw_root / "dark"
    if (dark / "capture_manifest.json").exists():
        man = sc.CaptureManifest.load(dark)
        stats = C.DarkStats()
        for (iso, t), ents in sorted(_groups(man, "dark").items()):
            frames = [load_dng(man.path(e.file), sidecar=man.sidecar(e)) for e in sorted(ents, key=lambda e: e.frame)]
            black_hint = frames[0].black_2x2 if black_hint is None else black_hint
            stats.add_group(iso, t, [f.bayer.astype(np.float64) for f in frames], frames[0].black_2x2)
        gain_fn = SensorModel(params).gain if params.gain_table or params.source == "calibration" else (lambda i: 1.0)
        d = C.fit_dark(stats, gain_fn)
        if d["read_noise_table"]:
            params.read_noise_table = d["read_noise_table"]       # dark frames beat PTC intercept
        params.row_noise_e = d["row_noise_e"]
        params.dark_rate_e_s = d["dark_rate_e_s"]
        dsnu = d["dsnu_e_s"]
        report["dark"] = {k: v for k, v in d.items() if k != "dsnu_e_s"}
        report["dark"]["black_by_setting"] = {f"{k[0]:g}/{k[1] * 1e3:g}ms": v.tolist() for k, v in stats.black_2x2.items()}
        log(f"  dark: read noise table {d['read_noise_table']}, row {d['row_noise_e']:.3f} e-, {d.get('notes')}")
    else:
        report["notes"].append(f"no dark captures in {dark}: read noise/dark current from the PTC intercept or defaults")

    if params.gain_table is None and params.read_noise_table is None:
        return None, dsnu, shading, report
    if params.gain_table is None:
        params.source = "calibration(partial: read noise only)"
    return params, dsnu, shading, report


def collect_pairs(dirs: List[Path], max_per_dir: int = 12, log=print) -> List[Pair]:
    pairs = []
    for d in dirs:
        if not (Path(d) / "capture_manifest.json").exists():
            continue
        man = sc.CaptureManifest.load(d)
        n = 0
        for e in man.entries:
            if e.tag not in ("pair", "raw", "val") or not e.file.lower().endswith(".dng"):
                continue
            if not (e.png_full or e.png):
                continue
            fr = load_dng(man.path(e.file), sidecar=man.sidecar(e))
            p = load_pair(d, fr)
            if p is not None:
                p.name = f"{Path(d).name}/{e.file}"
                pairs.append(p)
                n += 1
            if n >= max_per_dir:
                break
        log(f"  {d}: {n} pairs")
    return pairs


def fit_isp(pairs: List[Pair], base: ISPParams, device="cpu", log=print):
    if not pairs:
        raise ValueError("no (DNG, PNG) pairs available for ISP fitting")
    params, align, rep = fit_isp_from_pairs(pairs, base, device=device)
    log(f"  ISP fit: {rep['summary']}; alignment residual {rep['stages'].get('alignment', {}).get('residual_px_after')} px")
    return params, align, rep
