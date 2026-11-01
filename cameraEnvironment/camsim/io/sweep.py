"""Generic parser for Lattice frame-sweep folders.

Nothing here knows the grid: axes, downscale, averaging and white-balance axis are all read from
``manifest.json`` / ``metadata.csv``. The ``actual*`` values are always used (ISO is quantised,
focus 0 D becomes 0.1 D, ...).
"""
from __future__ import annotations

import csv
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .image import read_image_rgb


@dataclass
class Capture:
    index: int
    filename: str
    iso: float                      # actualIso (fallback requested)
    exposure_s: float               # actualExposureNs / 1e9
    focus_d: float                  # actualFocusDiopters
    requested_iso: float = float("nan")
    requested_exposure_s: float = float("nan")
    requested_focus_d: float = float("nan")
    kelvin: Optional[float] = None
    gains: Optional[Tuple[float, float, float, float]] = None   # R, G_even, G_odd, B
    frames_averaged: int = 1
    downscale: int = 1
    out_w: Optional[int] = None
    out_h: Optional[int] = None
    awb_state: Optional[int] = None
    settled: Optional[bool] = None
    timestamp_ns: Optional[int] = None

    @property
    def log2_iso(self):
        return float(np.log2(self.iso))

    @property
    def log2_exposure(self):
        return float(np.log2(self.exposure_s))


def _f(v, default=None):
    if v is None or v == "" or v == "null":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=None):
    x = _f(v)
    return default if x is None else int(round(x))


class Sweep:
    """A frame-sweep session folder (``framesweeps/<session>``)."""

    def __init__(self, root, manifest: dict, captures: List[Capture]):
        self.root = Path(root)
        self.name = self.root.name
        self.manifest = manifest or {}
        self.config = self.manifest.get("config", {}) or {}
        self.camera = self.manifest.get("camera", {}) or {}
        self.captures = captures
        self._by_key: Dict[tuple, List[Capture]] = {}
        for c in captures:
            self._by_key.setdefault(self._key(c.iso, c.exposure_s, c.focus_d, c.kelvin), []).append(c)

    # ---------------------------------------------------------------- construction
    @staticmethod
    def _key(iso, exp_s, focus, kelvin):
        return (round(float(iso), 3), round(float(exp_s) * 1e9), round(float(focus), 4),
                None if kelvin is None else round(float(kelvin)))

    @classmethod
    def load(cls, root) -> "Sweep":
        root = Path(root)
        if not root.is_dir():
            raise FileNotFoundError(f"sweep folder not found: {root}")
        manifest = {}
        mpath = root / "manifest.json"
        if mpath.exists():
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        caps: List[Capture] = []
        csv_path = root / "metadata.csv"
        if csv_path.exists():
            with open(csv_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    caps.append(cls._from_csv_row(row))
        elif manifest.get("captures"):
            for r in manifest["captures"]:
                caps.append(cls._from_manifest_record(r))
        else:
            raise FileNotFoundError(f"{root} has neither metadata.csv nor manifest.json captures[]")
        # fill defaults from config where a column is missing
        ds_cfg = _i(cls._cfg(manifest, "downscale"), 1)
        fa_cfg = _i(cls._cfg(manifest, "framesToAverage"), 1)
        for c in caps:
            if c.downscale is None:
                c.downscale = ds_cfg
            if c.frames_averaged is None:
                c.frames_averaged = fa_cfg
        return cls(root, manifest, caps)

    @staticmethod
    def _cfg(manifest, key):
        return (manifest.get("config") or {}).get(key)

    @staticmethod
    def _from_csv_row(r: dict) -> Capture:
        iso = _f(r.get("actualIso"), _f(r.get("requestedIso")))
        exp = _f(r.get("actualExposureNs"), _f(r.get("requestedExposureNs")))
        foc = _f(r.get("actualFocusDiopters"), _f(r.get("requestedFocusDiopters")))
        gains = None
        if _f(r.get("gainR")) is not None:
            gains = (_f(r.get("gainR")), _f(r.get("gainGEven"), 1.0), _f(r.get("gainGOdd"), 1.0),
                     _f(r.get("gainB"), 1.0))
        return Capture(
            index=_i(r.get("index"), 0), filename=r.get("filename", ""), iso=iso, exposure_s=exp / 1e9,
            focus_d=foc, requested_iso=_f(r.get("requestedIso"), float("nan")),
            requested_exposure_s=_f(r.get("requestedExposureNs"), float("nan")) / 1e9,
            requested_focus_d=_f(r.get("requestedFocusDiopters"), float("nan")),
            kelvin=_f(r.get("requestedKelvin")), gains=gains,
            frames_averaged=_i(r.get("framesAveraged")), downscale=_i(r.get("downscale")),
            out_w=_i(r.get("outputWidth")), out_h=_i(r.get("outputHeight")),
            awb_state=_i(r.get("awbState")),
            settled=None if r.get("settled") in (None, "") else str(r.get("settled")).lower() == "true",
            timestamp_ns=_i(r.get("timestampNs")))

    @staticmethod
    def _from_manifest_record(r: dict) -> Capture:
        g = r.get("actualColorGains")
        size = r.get("outputSize") or ""
        w = h = None
        if "x" in size:
            w, h = (int(v) for v in size.split("x"))
        iso = _f(r.get("actualIso"), _f(r.get("requestedIso")))
        exp = _f(r.get("actualExposureNs"), _f(r.get("requestedExposureNs")))
        foc = _f(r.get("actualFocusDiopters"), _f(r.get("requestedFocusDiopters")))
        return Capture(
            index=_i(r.get("index"), 0), filename=r.get("filename", ""), iso=iso, exposure_s=exp / 1e9,
            focus_d=foc, requested_iso=_f(r.get("requestedIso"), float("nan")),
            requested_exposure_s=_f(r.get("requestedExposureNs"), float("nan")) / 1e9,
            requested_focus_d=_f(r.get("requestedFocusDiopters"), float("nan")),
            kelvin=_f(r.get("requestedKelvin")),
            gains=tuple(g) if g and len(g) == 4 else None,
            frames_averaged=_i(r.get("framesAveraged")), downscale=_i(r.get("downscale")),
            out_w=w, out_h=h, awb_state=_i(r.get("awbState")), settled=r.get("settled"),
            timestamp_ns=_i(r.get("timestampNs")))

    # ---------------------------------------------------------------- queries
    def __len__(self):
        return len(self.captures)

    def focus_values(self, decimals: int = 4) -> List[float]:
        return sorted({round(c.focus_d, decimals) for c in self.captures})

    def iso_values(self) -> List[float]:
        return sorted({c.iso for c in self.captures})

    def exposure_values(self) -> List[float]:
        return sorted({c.exposure_s for c in self.captures})

    def nearest_focus(self, focus_d: float) -> float:
        vals = self.focus_values()
        return min(vals, key=lambda v: abs(v - focus_d))

    def has_focus(self, focus_d: float, tol: float = 0.02) -> bool:
        return abs(self.nearest_focus(focus_d) - focus_d) <= tol

    def points_at_focus(self, focus_d: float, tol: float = 0.02) -> List[Capture]:
        """Captures whose *actual* focus equals ``focus_d`` (within ``tol`` diopters)."""
        if not self.has_focus(focus_d, tol):
            raise ValueError(f"focus {focus_d} D is not in sweep {self.name}; "
                             f"available: {self.focus_values()}")
        f0 = self.nearest_focus(focus_d)
        return [c for c in self.captures if abs(c.focus_d - f0) <= 1e-3]

    def find(self, iso, exposure_s, focus_d, kelvin=None) -> Optional[Capture]:
        hits = self._by_key.get(self._key(iso, exposure_s, focus_d, kelvin))
        return hits[0] if hits else None

    def path(self, c: Capture) -> Path:
        return self.root / c.filename

    def load_png(self, c: Capture) -> np.ndarray:
        """uint8 HxWx3 RGB (alpha dropped)."""
        return read_image_rgb(self.path(c))

    def typical_gains(self, caps: Sequence[Capture]) -> Optional[Tuple[float, float, float, float]]:
        g = [c.gains for c in caps if c.gains is not None]
        if not g:
            return None
        return tuple(float(v) for v in np.median(np.array(g), axis=0))

    def downscale(self) -> int:
        ds = {c.downscale for c in self.captures if c.downscale}
        if len(ds) > 1:
            warnings.warn(f"sweep {self.name} mixes downscale factors {sorted(ds)}")
        return int(sorted(ds)[0]) if ds else 1

    def ensure_files(self, caps: Sequence[Capture], limit: int = 5):
        missing = [c.filename for c in caps if not self.path(c).exists()]
        if missing:
            warnings.warn(f"{len(missing)} sweep files missing, e.g. {missing[:limit]}")
        return missing
