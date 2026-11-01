"""Write simulated sweeps in the Lattice FrameSweep format (so Neural3A can read them)."""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

import numpy as np

from . import params as P
from .io.image import write_png_rgba, write_tiff16
from .io.sweep import Capture
from .simulator import CameraSimulator

CSV_COLUMNS = ["index", "filename", "requestedIso", "actualIso", "requestedExposureNs", "actualExposureNs",
               "requestedFocusDiopters", "actualFocusDiopters", "framesAveraged", "downscale", "outputWidth",
               "outputHeight", "requestedKelvin", "gainR", "gainGEven", "gainGOdd", "gainB", "awbState", "settled",
               "timestampNs"]


def filename_for(c: Capture) -> str:
    """Same convention as SweepStorage.filenameFor (index suffix keeps names unique)."""
    iso = str(int(round(c.iso))).zfill(4)
    us = str(int(round(c.exposure_s * 1e6))).zfill(6)
    fd = f"{c.focus_d:.2f}".replace(".", "p")
    wb = f"_wb{int(round(c.kelvin))}K" if c.kelvin else ""
    return f"iso{iso}_exp{us}us_fd{fd}D{wb}_avg{c.frames_averaged}_ds{c.downscale}_{str(c.index).zfill(4)}.png"


def dense_grid(sim: CameraSimulator, n_iso: int, n_exp: int, start_index: int = 100000) -> List[Capture]:
    """Extra log-uniform grid (ISO x exposure) at the raw focus for denser coverage than the sweep."""
    ds = sim.sweep.downscale()
    gains = sim.wb_gains
    out = []
    k = start_index
    for iso in P.log_grid(*P.ISO_RANGE, n_iso):
        for t in P.log_grid(P.EXPOSURE_RANGE_S[0], min(P.EXPOSURE_RANGE_S[1], sim.scene.t0), n_exp):
            c = Capture(index=k, filename="", iso=round(float(iso), 2), exposure_s=float(t), focus_d=sim.focus_d,
                        gains=gains, frames_averaged=1, downscale=ds)
            c.filename = filename_for(c)
            out.append(c)
            k += 1
    return out


def simulate_sweep(sim: CameraSimulator, out_dir, seed: int = 0, points: Optional[Sequence[Capture]] = None,
                   write_raw: bool = True, chunk: Optional[int] = None, progress: Optional[Callable] = print,
                   noisy: bool = True) -> dict:
    out_dir = Path(out_dir)
    (out_dir / "sim_png").mkdir(parents=True, exist_ok=True)
    if write_raw:
        (out_dir / "sim_raw").mkdir(parents=True, exist_ok=True)
    pts = list(points) if points is not None else sim.grid_points()
    if not pts:
        raise ValueError("no grid points to simulate")
    isos = [c.iso for c in pts]
    exps = [min(c.exposure_s, sim.scene.t0) for c in pts]
    wbs = [c.gains or sim.wb_gains for c in pts]
    records = []
    t0 = time.time()
    for part in sim.iter_render(isos, exps, wbs, noisy=noisy, seed=seed, chunk=chunk, keep_raw=write_raw):
        sl = part["sl"]
        for k, c in enumerate(pts[sl]):
            png = part["png"][k].permute(1, 2, 0).numpy()
            write_png_rgba(out_dir / "sim_png" / c.filename, png)
            if write_raw:
                write_tiff16(out_dir / "sim_raw" / (Path(c.filename).stem + ".tiff"), part["raw"][k].numpy())
            h, w = png.shape[:2]
            records.append((c, w, h))
        if progress:
            progress(f"  simulated {sl.stop if sl.stop < len(pts) else len(pts)}/{len(pts)} ({time.time() - t0:.0f}s)")
    _write_metadata(out_dir, sim, records)
    return {"n": len(records), "dir": str(out_dir)}


def _write_metadata(out_dir: Path, sim: CameraSimulator, records):
    src = sim.sweep.manifest
    caps_json = []
    with open(out_dir / "metadata.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(CSV_COLUMNS)
        for i, (c, w, h) in enumerate(records):
            g = c.gains or (None,) * 4
            row = [i, c.filename, c.iso, c.iso, int(round(c.exposure_s * 1e9)), int(round(c.exposure_s * 1e9)),
                   c.focus_d, c.focus_d, c.frames_averaged, c.downscale, w, h,
                   "" if c.kelvin is None else c.kelvin, *["" if v is None else v for v in g],
                   c.awb_state if c.awb_state is not None else "", "true", c.timestamp_ns or 0]
            wr.writerow(row)
            caps_json.append({"index": i, "filename": c.filename, "requestedIso": c.iso, "actualIso": c.iso,
                              "requestedExposureNs": int(round(c.exposure_s * 1e9)),
                              "actualExposureNs": int(round(c.exposure_s * 1e9)),
                              "requestedFocusDiopters": c.focus_d, "actualFocusDiopters": c.focus_d,
                              "framesAveraged": c.frames_averaged, "downscale": c.downscale,
                              "outputSize": f"{w}x{h}", "requestedKelvin": c.kelvin,
                              "actualColorGains": list(c.gains) if c.gains else None, "awbState": c.awb_state,
                              "settled": True, "timestampNs": c.timestamp_ns or 0})
    cfg = dict(sim.sweep.config)
    cfg.update({"isoValues": sorted({c.iso for c, _, _ in records}),
                "exposureValuesNs": sorted({int(round(c.exposure_s * 1e9)) for c, _, _ in records}),
                "focusValuesDiopters": [sim.focus_d], "totalCaptures": len(records), "totalFrames": len(records),
                "outputFormat": "PNG", "framesToAverage": 1})
    manifest = {"appVersion": "camsim-simulated", "sessionDir": out_dir.name, "camera": src.get("camera", {}),
                "config": cfg, "captures": caps_json,
                "simulation": {"source_sweep": sim.sweep.name, "sensor_params_source": sim.sensor.params.source,
                               "scene_mode": sim.scene.mode, "notes": sim.notes}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
