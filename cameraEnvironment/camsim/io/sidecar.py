"""Per-capture JSON sidecars and ``capture_manifest.json`` written by the Android capture test.

Schema (all keys of ``result`` are CaptureResult key names, e.g. ``android.sensor.sensitivity``)::

    {"file": "iso0050_exp100000us_fd1p00D_raw_f00.dng", "tag": "raw", "frame": 0,
     "png": "..._f00.png", "pngFull": "..._f00_full.png", "downscale": 2,
     "request": {"iso":50, "exposureNs":100000000, "focusD":1.0, "frameDurationNs":...},
     "raw": {"width":4080,"height":3060,"cfa":"RGGB","blackLevelPattern":[..],"whiteLevel":1023},
     "result": {...}}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

K_ISO = "android.sensor.sensitivity"
K_EXP = "android.sensor.exposureTime"
K_FOCUS = "android.lens.focusDistance"
K_NOISE = "android.sensor.noiseProfile"
K_DYN_BLACK = "android.sensor.dynamicBlackLevel"
K_DYN_WHITE = "android.sensor.dynamicWhiteLevel"
K_GAINS = "android.colorCorrection.gains"
K_CCM = "android.colorCorrection.transform"
K_TONEMAP_MODE = "android.tonemap.mode"
K_TONEMAP_CURVE = "android.tonemap.curve"
K_BOOST = "android.control.postRawSensitivityBoost"
K_SHADING_MAP = "android.statistics.lensShadingCorrectionMap"


def read_json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sidecar_path_for(path) -> Path:
    return Path(path).with_suffix(".json")


def load_sidecar(path) -> Optional[dict]:
    p = Path(path)
    if p.suffix.lower() != ".json":
        p = sidecar_path_for(p)
    return read_json(p) if p.exists() else None


def result_value(sidecar: Optional[dict], key: str, default=None):
    if not sidecar:
        return default
    return (sidecar.get("result") or {}).get(key, default)


def gains_from_sidecar(sidecar: Optional[dict]):
    g = result_value(sidecar, K_GAINS)
    if isinstance(g, dict):
        g = [g.get("red"), g.get("greenEven"), g.get("greenOdd"), g.get("blue")]
    if g and len(g) == 4 and all(v is not None for v in g):
        return tuple(float(v) for v in g)
    return None


@dataclass
class CaptureEntry:
    file: str
    tag: str
    frame: int = 0
    iso: Optional[float] = None
    exposure_s: Optional[float] = None
    focus_d: Optional[float] = None
    png: Optional[str] = None
    png_full: Optional[str] = None
    json_file: Optional[str] = None
    downscale: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def stem(self):
        return Path(self.file).stem


class CaptureManifest:
    """Reader for ``capture_manifest.json`` in a raw-capture folder."""

    def __init__(self, root, data: dict):
        self.root = Path(root)
        self.data = data
        self.mode = data.get("mode")
        self.entries: List[CaptureEntry] = []
        for e in data.get("entries", []):
            self.entries.append(CaptureEntry(
                file=e["file"], tag=e.get("tag", "raw"), frame=int(e.get("frame", 0)),
                iso=e.get("iso"), exposure_s=(e["exposureNs"] / 1e9 if e.get("exposureNs") else None),
                focus_d=e.get("focusD"), png=e.get("png"), png_full=e.get("pngFull"),
                json_file=e.get("json"), downscale=e.get("downscale"),
                extra={k: v for k, v in e.items()
                       if k not in {"file", "tag", "frame", "iso", "exposureNs", "focusD", "png",
                                    "pngFull", "json", "downscale"}}))

    @classmethod
    def load(cls, root) -> "CaptureManifest":
        root = Path(root)
        p = root / "capture_manifest.json"
        if p.exists():
            return cls(root, read_json(p))
        # No manifest: treat every DNG as a plain 'raw' frame.
        entries = []
        for i, f in enumerate(sorted(root.glob("*.dng"))):
            entries.append({"file": f.name, "tag": "raw", "frame": i,
                            "json": f.with_suffix(".json").name if f.with_suffix(".json").exists() else None})
        return cls(root, {"mode": "scene", "entries": entries})

    def by_tag(self, tag: str) -> List[CaptureEntry]:
        return sorted([e for e in self.entries if e.tag == tag], key=lambda e: (e.exposure_s or 0, e.frame))

    def path(self, name: str) -> Path:
        return self.root / name

    def sidecar(self, e: CaptureEntry) -> Optional[dict]:
        if e.json_file and self.path(e.json_file).exists():
            return read_json(self.path(e.json_file))
        return load_sidecar(self.path(e.file))
