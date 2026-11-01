"""Public API: CameraSimulator.

    sim = CameraSimulator.from_dirs("framesweeps/<s>", "rawframes/<s>", "calibration")
    out = sim.render(iso=400, exposure_s=0.01)         # {"raw", "png", "clip_mask", "png_clip_mask"}
    batch = sim.render_batch(isos, exposures)
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn.functional as F

from . import params as P
from .align import Alignment
from .calibration import Calibration
from .io import sidecar as sc
from .io.dng import RawFrame
from .io.sweep import Capture, Sweep
from .isp.pipeline import ISPModel, ISPParams, default_isp_params
from .render import RawSimulator, auto_chunk
from .sensor.model import SensorModel, SensorParams
from .sensor.source import SceneRadiance, load_scene

_FD = re.compile(r"fd(\d+)p(\d+)D")


def focus_from_name(name: str) -> Optional[float]:
    m = _FD.search(name)
    return float(f"{m.group(1)}.{m.group(2)}") if m else None


def pick_device(device: Optional[str] = None) -> torch.device:
    if device:
        return torch.device(device)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


class CameraSimulator:
    def __init__(self, sweep: Sweep, scene: SceneRadiance, sensor: SensorModel, isp: ISPModel,
                 focus_d: float, wb_gains: Optional[Tuple[float, ...]], notes: List[str],
                 calibration: Optional[Calibration] = None, iso_range=P.ISO_RANGE,
                 exposure_range=P.EXPOSURE_RANGE_S):
        self.sweep, self.scene, self.sensor, self.isp = sweep, scene, sensor, isp
        self.focus_d, self.wb_gains, self.notes = focus_d, wb_gains, notes
        self.calibration = calibration
        self.raw_sim = RawSimulator(scene, sensor, iso_range, exposure_range)
        self.device = sensor.device

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_dirs(cls, sweep_dir, raw_dir=None, calib_dir=None, device: Optional[str] = None,
                  frame: Optional[RawFrame] = None, scene: Optional[SceneRadiance] = None,
                  focus_d: Optional[float] = None, prefer: str = "auto",
                  fit_from_pair: bool = True) -> "CameraSimulator":
        """Build from ``framesweeps/<s>``, ``rawframes/<s>`` and ``calibration[/<device>]``.

        ``frame`` / ``scene`` may be passed instead of ``raw_dir`` (synthetic data, tests).
        """
        dev = pick_device(device)
        notes: List[str] = []
        sweep = Sweep.load(sweep_dir)
        cal = Calibration.load(calib_dir)
        if cal.root is None:
            notes.append("WARNING: no calibration found; using the DNG NoiseProfile and a fitted/fallback tone curve")
        if scene is None and frame is not None:
            scene = SceneRadiance.from_frame(frame)
        sensor_params = cal.sensor
        if scene is None:
            if raw_dir is None:
                raise ValueError("need raw_dir, frame or scene")
            tmp = None
            if sensor_params is not None:
                tmp = SensorModel(sensor_params, dev, cal.dsnu_e_s)
            scene = load_scene(raw_dir, tmp, focus_d=focus_d, prefer=prefer)
        frame = scene.frame
        if frame is None:
            raise ValueError("scene has no source frame")
        if sensor_params is None:
            sensor_params = SensorParams.from_noise_profile(frame)
        notes += [f"sensor params source: {sensor_params.source}"] + sensor_params.notes
        sensor = SensorModel(sensor_params, dev, cal.dsnu_e_s)
        notes += scene.notes

        # focus of the raw frame must be one of the sweep's focus values
        f = focus_d if focus_d is not None else frame.focus_d
        if f is None and frame.path:
            f = focus_from_name(Path(frame.path).name)
        if f is None:
            raise ValueError("cannot determine the raw frame's focus; pass focus_d=")
        if not sweep.has_focus(f):
            raise ValueError(f"raw frame focus {f} D is not in sweep {sweep.name}: {sweep.focus_values()}")
        f = sweep.nearest_focus(f)
        pts = sweep.points_at_focus(f)
        gains = frame.wb_gains or sweep.typical_gains(pts)
        sweep_gains = sweep.typical_gains(pts)
        if frame.wb_gains and sweep_gains and np.max(np.abs(np.array(frame.wb_gains) / np.array(sweep_gains) - 1)) > 0.03:
            notes.append(f"WARNING: raw-frame WB gains {frame.wb_gains} differ from the sweep's {sweep_gains} by >3 %")

        # ISP: calibration > fit from the scene's own pair > fallback
        isp_params = cal.isp or default_isp_params(frame)
        if cal.isp is None:
            notes += isp_params.notes
        isp = ISPModel(isp_params, dev, cal.alignment)
        sim = cls(sweep, scene, sensor, isp, f, gains, notes, cal)
        if cal.isp is None and fit_from_pair and raw_dir is not None:
            sim._fit_from_scene_pair(raw_dir)
        return sim

    def _fit_from_scene_pair(self, raw_dir):
        """Degraded mode: fit the tone curve (and alignment) from the scene's own ISO50/100 ms pair."""
        from .isp.fit import fit_isp_from_pairs, load_pair
        try:
            pair = load_pair(raw_dir, self.scene.frame)
        except Exception as e:                       # pragma: no cover - depends on capture layout
            self.notes.append(f"no paired PNG for the scene frame ({e}); tone curve stays at the sRGB fallback")
            return
        if pair is None:
            self.notes.append("no paired PNG for the scene frame; tone curve stays at the sRGB fallback")
            return
        params, align, rep = fit_isp_from_pairs([pair], self.isp.params, device=self.device,
                                                alignment=self.calibration.alignment if self.calibration else None)
        self.isp = ISPModel(params, self.device, align)
        self.notes.append(f"ISP tone curve fitted from the scene pair only (degraded mode): {rep.get('summary', '')}")

    # ------------------------------------------------------------------ queries
    def grid_points(self, iso_range=None, exposure_range=None) -> List[Capture]:
        ir = iso_range or self.raw_sim.iso_range
        er = exposure_range or self.raw_sim.exposure_range
        return [c for c in self.sweep.points_at_focus(self.focus_d)
                if ir[0] <= c.iso <= ir[1] and P.in_range(c.iso, c.exposure_s, ir, er)
                and c.exposure_s <= self.scene.t0 * (1 + P.EXPOSURE_REL_TOL)]

    def _out_hw(self) -> Tuple[int, int]:
        return self.isp.alignment.out_hw or tuple(self.scene.shape)

    # ------------------------------------------------------------------ rendering
    def iter_render(self, isos: Sequence[float], exposures: Sequence[float], wbs=None, noisy: bool = True,
                    seed: int = 0, chunk: Optional[int] = None, downscale: Optional[int] = None,
                    keep_raw: bool = True) -> Iterator[dict]:
        """Yield dict chunks (tensors on CPU): ``raw`` (b,H,W) float, ``png`` (b,3,h,w) uint8,
        ``clip_mask`` (b,H,W) bool, ``png_clip_mask`` (b,h,w) bool, plus ``sl`` (slice)."""
        ds = downscale or self.sweep.downscale()
        H, W = self.scene.shape
        chunk = chunk or auto_chunk(*self._out_hw(), self.device, bytes_per_pixel=220)
        n = len(isos)
        if wbs is None:
            wbs = [self.wb_gains or (1.0, 1.0, 1.0, 1.0)] * n
        wbs = np.asarray(wbs, np.float32)
        for sl, raw, clip in self.raw_sim.render(isos, exposures, noisy=noisy, seed=seed, chunk=chunk):
            wb = wbs[sl]
            png = self.isp(raw, wb, downscale=ds, iso=isos[sl], exposure=exposures[sl])
            # clip mask on the PNG grid: dilate by the demosaic footprint, resample, pool
            m = F.max_pool2d(clip.float()[:, None], 5, 1, 2)
            m = self.isp.alignment.warp(m, self._out_hw())
            pm = F.max_pool2d(m, ds, ds)[:, 0] > 0.01 if ds > 1 else m[:, 0] > 0.01
            out = {"sl": sl, "png": png.clamp(0, 255).to(torch.uint8).cpu(), "png_clip_mask": pm.cpu(),
                   "clip_mask": clip.cpu()}
            if keep_raw:
                out["raw"] = raw.cpu()
            yield out

    def render_batch(self, isos, exposures, wbs=None, noisy=True, seed=0, chunk=None, downscale=None,
                     keep_raw=True) -> Dict[str, torch.Tensor]:
        parts = list(self.iter_render(isos, exposures, wbs, noisy, seed, chunk, downscale, keep_raw))
        out = {k: torch.cat([p[k] for p in parts]) for k in parts[0] if k != "sl"}
        out["iso"] = torch.tensor(list(isos), dtype=torch.float32)
        out["exposure_s"] = torch.tensor(list(exposures), dtype=torch.float32)
        return out

    def render(self, iso: float, exposure_s: float, return_raw: bool = True, noisy: bool = True, seed: int = 0,
               wb=None, downscale=None) -> Dict[str, torch.Tensor]:
        """Single (iso, exposure) -> unbatched tensors."""
        b = self.render_batch([iso], [exposure_s], None if wb is None else [wb], noisy, seed, 1, downscale, return_raw)
        return {k: (v[0] if v.ndim > 0 and k not in () else v) for k, v in b.items()}
