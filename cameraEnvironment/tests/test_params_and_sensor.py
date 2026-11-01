import math

import numpy as np
import pytest
import torch

from camsim import params as P
from camsim.sensor import calibrate as C
from camsim.sensor.model import SensorModel, SensorParams, sample_read_noise, tukey_lambda_var
from camsim.sensor.source import SceneRadiance
from camsim.render import RawSimulator
from camsim.synthetic import make_raw_frame


def flat_frame(h=256, w=256, **kw):
    rgb = np.full((h, w, 3), 0.4, np.float32)
    kw.setdefault("wb", (1, 1, 1, 1))
    return make_raw_frame(h, w, scene_rgb=rgb, **kw)


def test_log_helpers_and_ranges():
    g = P.log_grid(1e-3, 0.1, 5)
    assert np.allclose(np.diff(np.log2(g)), np.diff(np.log2(g))[0])
    s = P.sample_log_uniform(50, 1600, 5000, seed=1)
    assert s.min() >= 50 and s.max() <= 1600
    # log-uniform: half of the samples below the geometric mean
    assert abs((s < math.sqrt(50 * 1600)).mean() - 0.5) < 0.03
    assert P.ev(8.0) == 3.0
    with pytest.raises(ValueError):
        P.validate_iso(3200)
    with pytest.raises(ValueError):
        P.validate_exposure(0.0005)
    with pytest.raises(ValueError):
        P.validate_exposure(0.1, t_source=0.05)       # exposure longer than the source
    assert P.validate_exposure(0.1001, t_source=0.1)   # quantisation tolerance


@pytest.mark.parametrize("p", [0.5, 0.1])
def test_poisson_thinning_fano(p):
    """Thinned Poisson source: variance / mean of the electron count stays 1."""
    f = flat_frame(read_e=0.0, gain=0.25, full_e=1500.0 / 0.4)       # ~1500 e- per pixel at t0
    scene = SceneRadiance.from_frame(f)
    sm = SensorModel(SensorParams(gain_ref=0.25, read_noise_e=0.0))
    raw, _ = sm.simulate(scene, [50], [0.1 * p], noisy=True, generator=torch.Generator().manual_seed(1))
    d = (raw[0] - 64).numpy() / 0.25                                   # electrons
    mean, var = d.mean(), d.var()
    assert abs(mean - p * 1500) / (p * 1500) < 0.02
    assert abs(var / mean - 1.0) < 0.06                                 # Poisson: var = mean (+ quantisation)


def test_read_noise_budget_not_double_counted():
    """Single-frame source carries read noise; the target must have it exactly once."""
    g, re = 0.25, 3.0
    f = flat_frame(read_e=re, gain=g, full_e=2000.0 / 0.4, seed=3)
    scene = SceneRadiance.from_frame(f)
    sm = SensorModel(SensorParams(gain_ref=g, read_noise_e=re))
    p = 0.5
    raw, _ = sm.simulate(scene, [50], [0.1 * p], noisy=True, generator=torch.Generator().manual_seed(2))
    d = (raw[0] - 64).numpy()
    expected = g ** 2 * (p * 2000.0) + (g * re) ** 2 + 1 / 12
    assert abs(d.var() / expected - 1) < 0.06
    # at p=1 and the same ISO nothing may be added to the source noise
    raw1, _ = sm.simulate(scene, [50], [0.1], noisy=True, generator=torch.Generator().manual_seed(3))
    assert abs(raw1[0].numpy().var() / f.bayer.var() - 1) < 0.02


def test_gain_and_read_noise_roundtrip_via_ptc():
    """Simulated variance-vs-mean slope = g(ISO), intercept = read^2; recovered by calibrate.PTCAccumulator."""
    h, w = 256, 256
    ramp = np.tile(np.linspace(0.0, 800.0, w, dtype=np.float32), (h, 1))        # DN above black at (50, t0)
    scene = SceneRadiance(ramp, np.zeros((h, w), bool), np.full((h, w), 64, np.float32), 1023.0, 50.0, 0.1, "RGGB",
                          mode="burst", n_eff=1e9)
    params = SensorParams(gain_ref=0.25, read_noise_e=3.0)
    sm = SensorModel(params)
    iso, t = 400, 0.1 / 8
    acc = C.PTCAccumulator(n_bins=24)
    for seed in range(6):
        gen = torch.Generator().manual_seed(seed)
        r, _ = sm.simulate(scene, [iso, iso], [t, t], noisy=True, generator=gen)
        acc.add_pair(r[0].numpy().astype(np.float64), r[1].numpy().astype(np.float64), np.full((2, 2), 64.0), 1023.0)
    fit = acc.fit(min_count=500)
    g_true = 0.25 * 8
    assert abs(fit["gain_dn_per_e"] / g_true - 1) < 0.04
    assert abs(fit["read_dn"] / (3.0 * g_true) - 1) < 0.15
    assert fit["r2"] > 0.98


def test_clipping_masks_and_exposure_rejection():
    f = flat_frame(h=64, w=64, full_e=1e5, gain=0.25)       # saturated source
    scene = SceneRadiance.from_frame(f)
    assert scene.clip_mask.all()
    rs = RawSimulator(scene, SensorModel(SensorParams()))
    raw, clip = rs.render_all([50, 800], [0.1, 0.002], noisy=True, seed=0)
    assert clip.all() and float(raw.max()) <= 1023.0
    with pytest.raises(ValueError):
        rs.render_all([50], [0.2])                           # longer than the source exposure
    with pytest.raises(ValueError):
        rs.render_all([3200], [0.05])
    # partial clip: only the bright half
    f2 = flat_frame(h=64, w=64)
    f2.bayer[:, 32:] = 1023
    sc2 = SceneRadiance.from_frame(f2)
    assert sc2.clip_mask[:, 32:].all() and not sc2.clip_mask[:, :30].any()
    assert abs(sc2.clipped_fraction - 0.5) < 0.02


def test_high_iso_saturates_not_wraps():
    f = flat_frame(h=64, w=64, full_e=3000.0 / 0.4)
    sm = SensorModel(SensorParams(gain_ref=0.25))
    raw, clip = sm.simulate(SceneRadiance.from_frame(f), [1600], [0.1], noisy=True, generator=torch.Generator().manual_seed(0))
    assert float(raw.max()) == 1023.0 and bool(clip.any())


def test_digital_gain_leaves_gaps_and_is_detected():
    f = flat_frame(h=256, w=256, full_e=3500.0, read_e=3.0, gain=0.25)
    sc = SceneRadiance.from_frame(f)
    analog = SensorModel(SensorParams(gain_ref=0.25, read_noise_e=3.0))
    digi = SensorModel(SensorParams(gain_ref=0.25, read_noise_e=3.0, analog_iso_max=100))
    # same total gain; ISO 400 with analog max 100 => digital x4 on integers
    kw = dict(noisy=True, generator=torch.Generator().manual_seed(0))
    ra, _ = analog.simulate(sc, [400], [0.1 / 8], **kw)
    kw = dict(noisy=True, generator=torch.Generator().manual_seed(0))
    rd, _ = digi.simulate(sc, [400], [0.1 / 8], **kw)
    occ_a = C.detect_gain_gaps(ra[0].numpy(), white=1023, black=64)["occupancy"]
    occ_d = C.detect_gain_gaps(rd[0].numpy(), white=1023, black=64)["occupancy"]
    assert occ_a > 0.9 and occ_d < 0.4
    law = C.fit_gain_law({100.0: {"gain_dn_per_e": .5}, 400.0: {"gain_dn_per_e": 2.0}}, {100.0: 0.97, 400.0: 0.25})
    assert law["analog_iso_max"] == 100.0


def test_tukey_lambda_noise_has_requested_std_and_heavier_tails():
    gen = torch.Generator().manual_seed(0)
    n = sample_read_noise((400000,), 2.0, "tukey", -0.1, "cpu", gen)
    g = sample_read_noise((400000,), 2.0, "gaussian", 0.0, "cpu", gen)
    assert abs(float(n.std()) / 2.0 - 1) < 0.02
    kurt = lambda x: float(((x - x.mean()) ** 4).mean() / x.var() ** 2)
    assert kurt(n) > kurt(g) + 0.3
    assert tukey_lambda_var(0.0) == pytest.approx(math.pi ** 2 / 3)


def test_seed_determinism_and_chunk_independence_of_shapes():
    f = flat_frame(h=64, w=64)
    rs = RawSimulator(SceneRadiance.from_frame(f), SensorModel(SensorParams()))
    a, _ = rs.render_all([100, 200], [0.05, 0.02], seed=7)
    b, _ = rs.render_all([100, 200], [0.05, 0.02], seed=7)
    c, _ = rs.render_all([100, 200], [0.05, 0.02], seed=8)
    assert torch.equal(a, b) and not torch.equal(a, c)
    d, _ = rs.render_all([100, 200], [0.05, 0.02], seed=7, chunk=1)
    assert d.shape == a.shape


def test_dark_current_and_dsnu_added_for_target_only():
    f = flat_frame(h=64, w=64, full_e=1000.0, read_e=0.0, noisy=False)        # tiny, noise-free signal
    f.bayer += 100.0 * 0.1 * 0.25                                    # source already holds the mean dark signal
    f.bayer[10, 10] += 5000.0 * 0.1 * 0.25                          # ... and the hot pixel
    sc = SceneRadiance.from_frame(f)
    dsnu = np.zeros((64, 64), np.float32)
    dsnu[10, 10] = 5000.0                                           # hot pixel, e/s
    sm = SensorModel(SensorParams(gain_ref=0.25, read_noise_e=0.0, dark_rate_e_s=100.0), dsnu_e_s=dsnu)
    raw, _ = sm.simulate(sc, [50], [0.05], noisy=False)
    # hot pixel: 5000 e/s * 0.05 s * 0.25 DN/e = 62.5 DN over its neighbours
    d = float(raw[0, 10, 10] - raw[0, 10, 12])
    assert 55 < d < 70                                              # excess scaled to the target exposure (62.5)
