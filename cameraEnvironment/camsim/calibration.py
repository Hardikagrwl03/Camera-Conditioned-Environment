"""On-disk calibration bundle: ``calibration/<device>/``.

Files: ``sensor_calibration.json`` (+ ``dsnu_e_per_s.npy``), ``isp_params.json``,
``alignment.json``, ``fit_report.json``. Every part is optional; missing parts trigger the
documented fallback chain (calibration -> DNG NoiseProfile -> defaults) and a warning.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np

from .align import Alignment
from .isp.pipeline import ISPParams
from .sensor.model import SensorParams


@dataclass
class Calibration:
    root: Optional[Path] = None
    sensor: Optional[SensorParams] = None
    dsnu_e_s: Optional[np.ndarray] = None
    isp: Optional[ISPParams] = None
    alignment: Optional[Alignment] = None
    report: dict = field(default_factory=dict)

    @staticmethod
    def resolve(calib_dir, device: Optional[str] = None) -> Optional[Path]:
        """``calibration/`` or ``calibration/<device>/``; with one device subfolder it is picked."""
        if calib_dir is None:
            return None
        root = Path(calib_dir)
        if not root.exists():
            return None
        markers = ("sensor_calibration.json", "isp_params.json", "alignment.json")
        if device and (root / device).is_dir():
            return root / device
        if any((root / m).exists() for m in markers):
            return root
        subs = [d for d in root.iterdir() if d.is_dir() and any((d / m).exists() for m in markers)]
        return subs[0] if len(subs) == 1 else (root if not subs else None)

    @classmethod
    def load(cls, calib_dir, device: Optional[str] = None) -> "Calibration":
        root = cls.resolve(calib_dir, device)
        cal = cls(root=root)
        if root is None:
            return cal
        f = root / "sensor_calibration.json"
        if f.exists():
            cal.sensor = SensorParams.from_dict(json.loads(f.read_text()))
        f = root / "dsnu_e_per_s.npy"
        if f.exists():
            cal.dsnu_e_s = np.load(f)
        f = root / "isp_params.json"
        if f.exists():
            cal.isp = ISPParams.load(f)
        f = root / "alignment.json"
        if f.exists():
            cal.alignment = Alignment.load(f)
        f = root / "fit_report.json"
        if f.exists():
            cal.report = json.loads(f.read_text())
        return cal

    def save(self, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        if self.sensor is not None:
            (root / "sensor_calibration.json").write_text(json.dumps(self.sensor.to_dict(), indent=2))
        if self.dsnu_e_s is not None:
            np.save(root / "dsnu_e_per_s.npy", self.dsnu_e_s.astype(np.float32))
        if self.isp is not None:
            self.isp.save(root / "isp_params.json")
        if self.alignment is not None:
            self.alignment.save(root / "alignment.json")
        (root / "fit_report.json").write_text(json.dumps(self.report, indent=2, default=str))
