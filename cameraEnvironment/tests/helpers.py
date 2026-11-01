"""Test helpers: tiny synthetic sweeps with arbitrary grids."""
import csv
import json
from pathlib import Path

import numpy as np

from camsim.io.image import write_png_rgba


def make_sweep_dir(root: Path, isos, exps_ns, focuses, ds=2, size=(48, 64), kelvin=(None,), gains=(2.1, 1.0, 1.0, 1.6),
                   png_fn=None, name="synth"):
    """Write manifest.json + metadata.csv (+ PNGs when png_fn(iso, exp_ns, focus) -> HxWx3 uint8 is given)."""
    root = Path(root) / name
    root.mkdir(parents=True, exist_ok=True)
    caps, idx = [], 0
    for f in focuses:
        for k in kelvin:
            for iso in isos:
                for e in exps_ns:
                    fn = f"iso{iso:04d}_exp{e // 1000:06d}us_fd{f:.2f}D_avg1_ds{ds}_{idx:04d}.png".replace(".", "p", 1) \
                        if False else f"iso{iso:04d}_exp{e // 1000:06d}us_fd{str(f'{f:.2f}').replace('.', 'p')}D_avg1_ds{ds}_{idx:04d}.png"
                    caps.append(dict(index=idx, filename=fn, requestedIso=iso, actualIso=iso, requestedExposureNs=e,
                                     actualExposureNs=e, requestedFocusDiopters=f, actualFocusDiopters=f,
                                     framesAveraged=1, downscale=ds, outputSize=f"{size[1]}x{size[0]}",
                                     requestedKelvin=k, actualColorGains=list(gains), awbState=3, settled=True,
                                     timestampNs=idx))
                    if png_fn is not None:
                        write_png_rgba(root / fn, png_fn(iso, e, f))
                    idx += 1
    manifest = {"appVersion": "test", "sessionDir": name,
                "camera": {"cameraId": "0", "sensitivityRange": [12, 3200]},
                "config": {"isoValues": list(isos), "exposureValuesNs": list(exps_ns),
                           "focusValuesDiopters": list(focuses), "downscale": ds, "framesToAverage": 1,
                           "whiteBalanceKelvin": list(kelvin)},
                "captures": caps}
    (root / "manifest.json").write_text(json.dumps(manifest))
    with open(root / "metadata.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "filename", "requestedIso", "actualIso", "requestedExposureNs", "actualExposureNs",
                    "requestedFocusDiopters", "actualFocusDiopters", "framesAveraged", "downscale", "outputWidth",
                    "outputHeight", "requestedKelvin", "gainR", "gainGEven", "gainGOdd", "gainB", "awbState",
                    "settled", "timestampNs"])
        for c in caps:
            w.writerow([c["index"], c["filename"], c["requestedIso"], c["actualIso"], c["requestedExposureNs"],
                        c["actualExposureNs"], c["requestedFocusDiopters"], c["actualFocusDiopters"], 1, ds,
                        size[1], size[0], "" if c["requestedKelvin"] is None else c["requestedKelvin"], *gains, 3,
                        "true", c["index"]])
    return root
