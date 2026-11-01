"""Round trips for the calibration fitting: synthetic flat/dark DNG folders -> SensorParams -> save/load."""
import json

import numpy as np
import pytest

from camsim.calibration import Calibration
from camsim.fitting import fit_sensor
from camsim.io import sidecar as sc
from camsim.io.dng import RawFrame
from camsim.sensor.calibrate import DarkStats, detect_gain_gaps, fit_dark, fit_shading_from_flat
from camsim.sensor.model import SensorModel, SensorParams
from camsim.synthetic import make_raw_frame, write_synthetic_dng

H, W = 192, 256
G50, READ_E = 0.25, 3.0


def _write(root, name, frame, iso, t, tag, k, entries):
    write_synthetic_dng(root / f"{name}.dng", frame)
    (root / f"{name}.json").write_text(json.dumps({"result": {sc.K_ISO: iso, sc.K_EXP: int(t * 1e9), sc.K_FOCUS: 1.0}}))
    entries.append({"file": f"{name}.dng", "tag": tag, "frame": k, "iso": iso, "exposureNs": int(t * 1e9),
                    "focusD": 1.0, "json": f"{name}.json"})


def _flat_dir(root):
    root.mkdir(parents=True)
    grad = np.tile(np.linspace(0.05, 1.0, W, dtype=np.float32)[None, :, None], (H, 1, 3))
    entries = []
    for iso in (50, 100, 400):
        for i, t in enumerate(np.geomspace(0.01, 0.1, 8)):
            for k in range(2):
                f = make_raw_frame(H, W, iso=iso, exposure_s=t, gain=G50 * iso / 50, read_e=READ_E,
                                   full_e=3000.0 * (t / 0.1) * 50 / iso, scene_rgb=grad, wb=(1, 1, 1, 1),
                                   seed=1000 * iso + 10 * i + k)
                _write(root, f"iso{iso:04d}_exp{int(t * 1e6):06d}us_flat_f{k:02d}", f, iso, t, "flat", k, entries)
    (root / "capture_manifest.json").write_text(json.dumps({"mode": "flat", "entries": entries}))


def _dark_dir(root, rate=50.0, hot=(40, 60, 8000.0)):
    root.mkdir(parents=True)
    entries = []
    rng = np.random.default_rng(0)
    rates = np.full((H, W), rate)
    rates[hot[0], hot[1]] = hot[2]
    for iso in (50, 400):
        g = G50 * iso / 50
        for t in (0.001, 0.1):
            for k in range(4):
                e = rng.poisson(rates * t)
                d = np.round(g * e + rng.normal(0, READ_E * g, e.shape)) + 64
                fr = RawFrame(np.clip(d, 0, 1023), "RGGB", np.full((2, 2), 64.0), 1023.0, iso, t)
                _write(root, f"iso{iso:04d}_exp{int(t * 1e6):06d}us_dark_f{k:02d}", fr, iso, t, "dark", k, entries)
    (root / "capture_manifest.json").write_text(json.dumps({"mode": "dark", "entries": entries}))


def test_fit_sensor_from_capture_folders_and_roundtrip(tmp_path):
    raw_root = tmp_path / "calibration" / "DEV" / "raw_captures"
    _flat_dir(raw_root / "flat")
    _dark_dir(raw_root / "dark")
    params, dsnu, shading, rep = fit_sensor(raw_root, log=lambda *_: None)
    assert params.source == "calibration" and params.analog_iso_max is None
    table = dict(params.gain_table)
    for iso in (50, 100, 400):
        assert table[iso] == pytest.approx(G50 * iso / 50, rel=0.05)
    assert rep["gain_law"]["max_deviation_from_proportional"] < 0.08
    rn = dict(params.read_noise_table)
    for iso in (50, 400):
        assert rn[iso] == pytest.approx(READ_E * G50 * iso / 50, rel=0.2)
    assert params.dark_rate_e_s == pytest.approx(50, rel=0.5)
    assert dsnu is not None and dsnu[40, 60] > 5000 and (dsnu > 0).sum() < 20          # hot pixel isolated
    assert shading is not None and shading.shape[-1] == 4 and shading.min() >= 0.99
    # the fitted model drives a SensorModel: gain interpolates in log-log between table points
    sm = SensorModel(params, dsnu_e_s=dsnu)
    assert sm.gain(200) == pytest.approx(G50 * 4, rel=0.06)
    # save / load bundle
    cal = Calibration(root=raw_root.parent, sensor=params, dsnu_e_s=dsnu)
    cal.save(raw_root.parent)
    back = Calibration.load(tmp_path / "calibration")                 # auto-picks the single device folder
    assert back.sensor.gain_table == [tuple(x) for x in params.gain_table] and back.dsnu_e_s.shape == (H, W)
    assert Calibration.load(tmp_path / "missing").sensor is None


def test_fit_sensor_missing_folders_degrade(tmp_path):
    (tmp_path / "raw").mkdir()
    params, dsnu, shading, rep = fit_sensor(tmp_path / "raw", log=lambda *_: None)
    assert params is None and any("no flat" in n for n in rep["notes"]) and any("no dark" in n for n in rep["notes"])


def test_dark_stats_row_noise_detected():
    rng = np.random.default_rng(1)
    st = DarkStats()
    row = rng.normal(0, 2.0, (H, 1))                  # row noise 2 DN rms
    frames = [64 + row * 0 + rng.normal(0, 1.5, (H, W)) + rng.normal(0, 2.0, (H, 1)) for _ in range(2)]
    st.add_group(100, 0.001, frames, np.full((2, 2), 64.0))
    out = fit_dark(st, lambda iso: 0.5)
    assert out["row_noise_e"] == pytest.approx(2.0 / 0.5, rel=0.3)
