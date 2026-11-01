from pathlib import Path

import numpy as np
import pytest

from camsim.io import sidecar as sc
from camsim.io.dng import combine_gain_maps, load_dng, parse_opcode_list
from camsim.io.sweep import Sweep
from camsim.synthetic import make_raw_frame, write_synthetic_dng
from helpers import make_sweep_dir

DUMMY = Path(__file__).resolve().parent.parent / "framesweeps"


@pytest.mark.skipif(not DUMMY.exists() or not any(DUMMY.iterdir()), reason="no dummy framesweeps/ present")
def test_dummy_sweep_parses_without_loading_all_files():
    root = sorted(d for d in DUMMY.iterdir() if (d / "manifest.json").exists())[0]
    sw = Sweep.load(root)
    assert len(sw) == len(sw.captures) > 0
    f = sw.focus_values()
    assert len(f) >= 2
    pts = sw.points_at_focus(f[len(f) // 2])
    assert all(abs(c.focus_d - f[len(f) // 2]) < 1e-3 for c in pts)
    c = pts[0]
    assert c.gains is not None and c.downscale >= 1
    img = sw.load_png(c)                      # one file only
    assert img.dtype == np.uint8 and img.shape[2] == 3
    if c.out_w:
        assert img.shape[1] == c.out_w and img.shape[0] == c.out_h


def test_parser_has_no_hardcoded_grid(tmp_path):
    # a grid unlike the dummies: other ISOs, exposures, focuses, downscale 4, a WB axis
    isos = [64, 250, 1000]
    exps = [2_000_000, 7_000_000]
    foc = [0.0, 0.5, 4.0]
    root = make_sweep_dir(tmp_path, isos, exps, foc, ds=4, kelvin=(3000, 6500), name="odd")
    sw = Sweep.load(root)
    assert len(sw) == len(isos) * len(exps) * len(foc) * 2
    assert sw.iso_values() == [64.0, 250.0, 1000.0]
    assert sw.downscale() == 4
    assert sw.has_focus(0.5) and not sw.has_focus(2.0)
    with pytest.raises(ValueError):
        sw.points_at_focus(2.0)
    pts = sw.points_at_focus(0.5)
    assert len(pts) == 12 and {c.kelvin for c in pts} == {3000.0, 6500.0}
    assert sw.find(250, 0.007, 0.5, 6500).kelvin == 6500
    assert sw.typical_gains(pts) == pytest.approx((2.1, 1.0, 1.0, 1.6))


def test_manifest_only_sweep(tmp_path):
    root = make_sweep_dir(tmp_path, [100], [1_000_000], [1.0], ds=2, name="m")
    (root / "metadata.csv").unlink()
    sw = Sweep.load(root)
    assert len(sw) == 1 and sw.captures[0].gains[0] == pytest.approx(2.1) and sw.captures[0].out_w == 64


def test_actual_values_are_used(tmp_path):
    root = make_sweep_dir(tmp_path, [141], [1_000_000], [0.0], name="q")
    import json
    m = json.loads((root / "manifest.json").read_text())
    m["captures"][0]["actualIso"] = 140
    m["captures"][0]["actualFocusDiopters"] = 0.1
    (root / "manifest.json").write_text(json.dumps(m))
    (root / "metadata.csv").unlink()
    c = Sweep.load(root).captures[0]
    assert c.iso == 140 and c.focus_d == pytest.approx(0.1) and c.requested_iso == 141


def test_dng_roundtrip_with_tifffile_and_sidecar(tmp_path):
    f = make_raw_frame(64, 80, iso=50, exposure_s=0.1, seed=2)
    p = tmp_path / "iso0050_exp100000us_fd1p00D_raw_f00.dng"
    write_synthetic_dng(p, f)
    g = load_dng(p, prefer="tifffile", iso=50, exposure_s=0.1)
    assert g.cfa == "RGGB" and g.white_level == 1023 and np.all(g.black_2x2 == 64)
    assert np.array_equal(g.bayer, f.bayer)
    assert g.noise_profile is not None and g.noise_profile.shape == (4, 2)
    assert 21 in g.forward_matrix and np.allclose(g.forward_matrix[21], np.eye(3), atol=1e-3)
    assert g.as_shot_neutral == pytest.approx([0.5, 1.0, 0.625])
    # sidecar supplies ISO/exposure/focus/WB and overrides the black level
    side = {"result": {sc.K_ISO: 400, sc.K_EXP: 10_000_000, sc.K_FOCUS: 1.0, sc.K_GAINS: [2.1, 1, 1, 1.6],
                       sc.K_DYN_BLACK: [60, 61, 62, 63], sc.K_CCM: list(np.eye(3).reshape(-1))}}
    h = load_dng(p, sidecar=side, prefer="tifffile")
    assert h.iso == 400 and h.exposure_s == pytest.approx(0.01) and h.focus_d == 1.0
    assert h.wb_gains == (2.1, 1.0, 1.0, 1.6) and h.black_2x2.reshape(-1).tolist() == [60, 61, 62, 63]
    with pytest.raises(ValueError):
        load_dng(p, prefer="tifffile")            # no ISO/exposure anywhere


def test_gain_map_opcode_parse():
    import struct
    gains = np.linspace(1.0, 2.0, 12, dtype=np.float32).reshape(3, 4, 1)
    body = struct.pack(">10I", 0, 0, 6, 8, 0, 1, 2, 2, 3, 4) + struct.pack(">4d", 0.5, 0.25, 0.0, 0.0) + struct.pack(">I", 1)
    body += gains.astype(">f4").tobytes()
    blob = struct.pack(">I", 1) + struct.pack(">IIII", 9, 0x01030000, 1, len(body)) + body
    ops = parse_opcode_list(blob)
    assert ops[0]["name"] == "GainMap" and ops[0]["gains"].shape == (3, 4, 1)
    gm = combine_gain_maps(ops)
    assert gm.shape == (3, 4, 4) and np.allclose(gm[..., 0], gains[..., 0])


def test_capture_manifest_reader(tmp_path):
    import json
    (tmp_path / "capture_manifest.json").write_text(json.dumps({"mode": "scene", "entries": [
        {"file": "a.dng", "tag": "raw", "frame": 0, "iso": 50, "exposureNs": 100000000, "focusD": 1.0, "png": "a.png"},
        {"file": "b.dng", "tag": "brk", "frame": 0, "iso": 50, "exposureNs": 25000000, "focusD": 1.0}]}))
    m = sc.CaptureManifest.load(tmp_path)
    assert [e.file for e in m.by_tag("raw")] == ["a.dng"] and m.by_tag("brk")[0].exposure_s == pytest.approx(0.025)
