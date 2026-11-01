"""End-to-end on CPU with synthetic data: raw frame -> render -> ISP -> simulate_sweep -> validate -> report."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from camsim.io import sidecar as sc
from camsim.io.image import write_png_rgba
from camsim.io.sweep import Sweep
from camsim.isp.pipeline import ISPModel, default_isp_params
from camsim.report import write_report
from camsim.simulator import CameraSimulator
from camsim.sweepout import dense_grid, simulate_sweep
from camsim.synthetic import make_raw_frame, make_scene_rgb, write_synthetic_dng
from camsim.validation import Thresholds, validate_png
from helpers import make_sweep_dir

ISOS = [100, 400, 1600]
EXPS = [1_000_000, 10_000_000, 100_000_000]


def _sim_and_sweep(tmp_path):
    f = make_raw_frame(96, 128, iso=50, exposure_s=0.1, seed=4, full_e=1200.0)
    sweep_dir = make_sweep_dir(tmp_path, ISOS, EXPS, [0.0, 1.0], ds=2, size=(48, 64), name="sw")
    sim = CameraSimulator.from_dirs(sweep_dir, frame=f, focus_d=1.0, device="cpu", calib_dir=None)
    # "real" frames = the model with a different seed (so the metrics have something to find)
    for n, c in enumerate(sim.sweep.captures):
        if abs(c.focus_d - 1.0) > 1e-3:
            write_png_rgba(sweep_dir / c.filename, np.zeros((48, 64, 3), np.uint8))
            continue
        out = sim.render(c.iso, c.exposure_s, seed=1000 + n)
        write_png_rgba(sweep_dir / c.filename, out["png"].permute(1, 2, 0).numpy())
    return sim, sweep_dir


def test_render_api_shapes_and_range_checks(tmp_path):
    sim, _ = _sim_and_sweep(tmp_path)
    out = sim.render(400, 0.01)
    assert out["raw"].shape == (96, 128) and out["png"].shape == (3, 48, 64) and out["png"].dtype == torch.uint8
    assert out["clip_mask"].shape == (96, 128) and out["png_clip_mask"].shape == (48, 64)
    b = sim.render_batch([100, 200, 400], [0.1, 0.05, 0.001])
    assert b["png"].shape == (3, 3, 48, 64) and b["raw"].shape == (3, 96, 128)
    with pytest.raises(ValueError):
        sim.render(3200, 0.01)
    with pytest.raises(ValueError):
        sim.render(100, 0.0001)
    with pytest.raises(ValueError):
        sim.render(100, 0.2)
    # brightness grows with ISO and with exposure (log-scale monotonic)
    m = lambda iso, t: float(sim.render(iso, t, noisy=False)["png"].float().mean())
    assert m(100, 0.01) < m(400, 0.01) < m(1600, 0.01) and m(400, 0.002) < m(400, 0.02)
    # one stop of ISO == one stop of exposure in raw (same electrons*gain), noise-free
    r1 = sim.render(200, 0.02, noisy=False)["raw"] - 64
    r2 = sim.render(100, 0.04, noisy=False)["raw"] - 64
    assert float((r1 / r2.clamp_min(1e-3))[r2 > 5].median()) == pytest.approx(1.0, abs=0.02)


def test_simulate_validate_report(tmp_path):
    sim, sweep_dir = _sim_and_sweep(tmp_path)
    pts = sim.grid_points()
    assert len(pts) == 9 and all(abs(c.focus_d - 1.0) < 1e-3 for c in pts)

    out = tmp_path / "out"
    res = simulate_sweep(sim, out, seed=5, progress=None)
    assert res["n"] == 9
    sim_sw = Sweep.load(out)                                  # drops straight into the FrameSweep format
    assert len(sim_sw) == 9 and sim_sw.downscale() == 2 and sim_sw.focus_values() == [1.0]
    assert all((out / "sim_png" / c.filename).exists() for c in sim_sw.captures)
    assert len(list((out / "sim_raw").glob("*.tiff"))) == 9
    assert set(sim_sw.iso_values()) == set(map(float, ISOS))
    img = sim_sw.load_png(sim_sw.captures[0]) if (out / sim_sw.captures[0].filename).exists() else None
    from camsim.io.image import read_image_rgb
    assert read_image_rgb(out / "sim_png" / sim_sw.captures[0].filename).shape == (48, 64, 3)

    dense = dense_grid(sim, 3, 4)
    assert len(dense) == 12 and min(c.iso for c in dense) == pytest.approx(50) and max(c.exposure_s for c in dense) <= 0.1 + 1e-9
    r = simulate_sweep(sim, tmp_path / "dense", points=dense, write_raw=False, progress=None)
    assert r["n"] == 12

    result = validate_png(sim, tmp_path / "rep", K=2, thresholds=Thresholds(), progress=None, raw_dir=None)
    s = result["summary"]
    assert s["n_points"] == 9
    assert s["ev_error_full"]["mean_abs"] < 0.15
    assert 8 < s["psnr_full_mean"] < 80
    assert (tmp_path / "rep" / "metrics.csv").exists() and (tmp_path / "rep" / "summary.json").exists()
    p = write_report(result, tmp_path / "rep")
    html = p.read_text()
    assert "Pass / fail" in html and "reported, not enforced" in html
    assert json.loads((tmp_path / "rep" / "summary.json").read_text())["summary"]["n_points"] == 9


def test_missing_focus_is_an_error(tmp_path):
    f = make_raw_frame(32, 32, focus_d=3.0)
    d = make_sweep_dir(tmp_path, [100], [1_000_000], [0.0, 1.0], name="x")
    with pytest.raises(ValueError, match="not in sweep"):
        CameraSimulator.from_dirs(d, frame=f, focus_d=3.0, device="cpu")


def test_raw_dir_burst_bracket_and_degraded_tone_fit(tmp_path):
    """Directory-driven path: burst + bracket merge, paired PNG -> tone curve fitted in degraded mode."""
    import cv2
    smooth = cv2.GaussianBlur(make_scene_rgb(96, 128, 1234), (0, 0), 3.0)
    smooth[:, 100:] = 1.0                                          # overexposed strip: clips at 100 ms
    raw_dir = tmp_path / "rawframes" / "sw"
    raw_dir.mkdir(parents=True)
    entries = []

    def add(tag, frame, exp_s, seed, png_full=None):
        f = make_raw_frame(96, 128, iso=50, exposure_s=exp_s, seed=seed, scene_rgb=smooth, full_e=5200.0 * exp_s / 0.1)
        name = f"iso0050_exp{int(exp_s * 1e6):06d}us_fd1p00D_{tag}_f{frame:02d}"
        write_synthetic_dng(raw_dir / f"{name}.dng", f)
        (raw_dir / f"{name}.json").write_text(json.dumps({"result": {
            sc.K_ISO: 50, sc.K_EXP: int(exp_s * 1e9), sc.K_FOCUS: 1.0, sc.K_GAINS: [2.0, 1.0, 1.0, 1.6],
            sc.K_CCM: list(np.eye(3).reshape(-1))}}))
        e = {"file": f"{name}.dng", "tag": tag, "frame": frame, "iso": 50, "exposureNs": int(exp_s * 1e9), "focusD": 1.0,
             "json": f"{name}.json"}
        if png_full:
            e["pngFull"] = png_full
        entries.append(e)
        return f

    # the paired full-resolution PNG comes from a hidden "HAL": different tone curve than the sRGB fallback
    ref = make_raw_frame(96, 128, iso=50, exposure_s=0.1, seed=10, scene_rgb=smooth, full_e=5200.0, noisy=False)
    truth = default_isp_params(ref)
    u = np.linspace(0, 1, 1024)
    truth.tone_lut = np.tile((255 * np.clip(1.15 * u ** (2 / 1.6), 0, 1))[None], (3, 1))
    f0 = None
    for k in range(3):
        f = add("raw", k, 0.1, 10 + k, "pair_f00_full.png" if k == 0 else None)
        f0 = f0 or f
    for k in range(2):
        add("brk", k, 0.025, 20 + k)
    # PNG for frame 0 = hidden HAL applied to the *burst-free* frame 0 itself
    from camsim.io.dng import load_dng
    fr0 = load_dng(raw_dir / "iso0050_exp100000us_fd1p00D_raw_f00.dng", prefer="tifffile", iso=50, exposure_s=0.1)
    png = ISPModel(truth)(torch.from_numpy(fr0.bayer)[None], [(2.0, 1, 1, 1.6)], downscale=1)[0].permute(1, 2, 0).numpy().astype(np.uint8)
    write_png_rgba(raw_dir / "pair_f00_full.png", png)
    (raw_dir / "capture_manifest.json").write_text(json.dumps({"mode": "scene", "entries": entries}))

    sweep_dir = make_sweep_dir(tmp_path / "framesweeps", [100, 400], [10_000_000, 100_000_000], [1.0], ds=2, size=(48, 64), name="sw")
    sim = CameraSimulator.from_dirs(sweep_dir, raw_dir, calib_dir=tmp_path / "no_calibration", device="cpu")
    assert sim.scene.mode == "bracket" and sim.scene.n_eff == 3
    clipped_single = float((fr0.bayer >= 1023 - 5).mean())
    assert sim.scene.clipped_fraction < clipped_single          # the bracket recovered highlights
    assert any("fitted from the scene pair" in n for n in sim.notes)
    assert any("NOT calibrated" in n or "no calibration" in n.lower() for n in sim.notes)
    assert sim.isp.params.source == "fitted"
    # the fitted pipeline matches the hidden HAL far better than the sRGB fallback does
    wb = (2.0, 1, 1, 1.6)
    raw = torch.from_numpy(fr0.bayer)[None]
    core = (slice(8, -8), slice(8, -8))
    fitted = ISPModel(sim.isp.params, alignment=sim.isp.alignment)(raw, [wb], downscale=1)[0].permute(1, 2, 0).numpy()
    default = ISPModel(default_isp_params(fr0))(raw, [wb], downscale=1)[0].permute(1, 2, 0).numpy()
    e_fit = np.abs(fitted.astype(float) - png.astype(float))[core].mean()
    e_def = np.abs(default.astype(float) - png.astype(float))[core].mean()
    assert e_fit < 4.0 and e_fit < 0.5 * e_def
