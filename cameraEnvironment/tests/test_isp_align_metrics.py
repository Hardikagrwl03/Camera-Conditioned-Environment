import copy

import numpy as np
import pytest
import torch

from camsim import metrics as M
from camsim.align import Alignment, estimate_alignment
from camsim.isp.fit import Pair, fit_isp_from_pairs, fit_lut, pav_increasing
from camsim.isp.pipeline import ISPModel, ISPParams, default_isp_params, demosaic_mhc, srgb_oetf_lut
from camsim.synthetic import make_raw_frame, make_scene_rgb, mosaic


@pytest.mark.parametrize("cfa", ["RGGB", "BGGR", "GRBG", "GBRG"])
def test_demosaic_exact_on_constant_and_linear_ramps(cfa):
    h, w = 24, 32
    rgb = np.stack([np.full((h, w), 0.2), np.full((h, w), 0.5), np.full((h, w), 0.8)], -1).astype(np.float32)
    out = demosaic_mhc(torch.from_numpy(mosaic(rgb, cfa))[None], cfa)[0].numpy()
    assert np.abs(out - rgb.transpose(2, 0, 1)).max() < 1e-5
    ramp = np.tile(np.linspace(0.1, 0.9, w, dtype=np.float32), (h, 1))
    rgb = np.stack([ramp, ramp * 0.5, ramp * 0.7], -1)
    out = demosaic_mhc(torch.from_numpy(mosaic(rgb, cfa))[None], cfa)[0].numpy()
    inner = (slice(None), slice(4, -4), slice(4, -4))
    assert np.abs(out - rgb.transpose(2, 0, 1))[inner].max() < 2e-3        # MHC is exact on linear ramps


def test_isp_output_shapes_and_range():
    f = make_raw_frame(64, 96)
    isp = ISPModel(default_isp_params(f))
    raw = torch.from_numpy(f.bayer)[None].repeat(2, 1, 1)
    out = isp(raw, [(2.0, 1, 1, 1.6)] * 2, downscale=2)
    assert out.shape == (2, 3, 32, 48) and out.min() >= 0 and out.max() <= 255
    # brighter raw -> brighter PNG
    out2 = isp(raw * 0.5, [(2.0, 1, 1, 1.6)] * 2, downscale=1)
    out1 = isp(raw, [(2.0, 1, 1, 1.6)] * 2, downscale=1)
    assert out1.float().mean() > out2.float().mean()


def test_pav_and_lut_fit_monotone():
    y = np.array([1, 3, 2, 4, 3.5, 6.0])
    z = pav_increasing(y)
    assert np.all(np.diff(z) >= 0)
    rng = np.random.default_rng(0)
    lin = rng.random(50000)
    tgt = 255 * lin ** (1 / 2.2) + rng.normal(0, 2, lin.size)
    lut = fit_lut(lin, tgt)
    assert np.all(np.diff(lut) >= -1e-9)
    xs = np.array([0.05, 0.2, 0.5, 0.8])
    got = np.interp(np.sqrt(xs), np.linspace(0, 1, len(lut)), lut)
    assert np.abs(got - 255 * xs ** (1 / 2.2)).max() < 3


def test_isp_fit_recovers_a_known_hal_pipeline():
    """Generate 'PNGs' with a hidden tone curve + CCM, fit from a wrong default, compare."""
    torch.manual_seed(0)
    import cv2
    smooth = cv2.GaussianBlur(make_scene_rgb(128, 160, 1234), (0, 0), 3.0)      # chroma subsampling needs smooth chroma
    f = make_raw_frame(128, 160, noisy=False, seed=1, scene_rgb=smooth)
    base = default_isp_params(f)
    truth = copy.deepcopy(base)
    truth.ccm = np.array([[1.25, -0.15, -0.10], [-0.1, 1.2, -0.1], [-0.05, -0.2, 1.25]])
    u = np.linspace(0, 1, 1024)
    truth.tone_lut = np.tile((255 * np.clip(u ** (2 / 2.4) * 1.05, 0, 1))[None], (3, 1))     # not sRGB
    truth.chroma = "average"
    wb = (2.0, 1.0, 1.0, 1.6)
    png = ISPModel(truth)(torch.from_numpy(f.bayer)[None], [wb], downscale=1)[0].permute(1, 2, 0).numpy().astype(np.uint8)
    pair = Pair(f, png, 1, wb, "synthetic")
    params, align, rep = fit_isp_from_pairs([pair], base, n_samples=60000, per_channel=False, refine_ccm=True)
    assert rep["stages"]["tone_ccm"]["holdout_rmse_dn"] < 3.0
    assert rep["stages"]["chroma"]["chosen"] == "average"
    assert not rep["tone_varies"]
    # the full fitted pipeline reproduces the target frame
    out = ISPModel(params, alignment=align)(torch.from_numpy(f.bayer)[None], [wb], downscale=1)[0].permute(1, 2, 0).numpy()
    core = (slice(8, -8), slice(8, -8))
    assert np.abs(out.astype(float) - png.astype(float))[core].mean() < 3.0
    # default (sRGB) pipeline is clearly worse: the fit is doing something
    out0 = ISPModel(base)(torch.from_numpy(f.bayer)[None], [wb], downscale=1)[0].permute(1, 2, 0).numpy()
    assert np.abs(out0.astype(float) - png.astype(float))[core].mean() > 6.0


def test_alignment_recovers_shift_and_warp_identity():
    import cv2
    rgb = make_scene_rgb(256, 320, 5).mean(-1)
    rng = np.random.default_rng(0)
    tex = cv2.GaussianBlur(rng.random(rgb.shape).astype(np.float32), (0, 0), 2.0)
    ref = (rgb + 0.5 * (tex - tex.mean())).astype(np.float32)
    M_ = np.float32([[1, 0, 3.4], [0, 1, -2.2]])         # warpAffine: sim(x) = ref(x - t)  =>  ref(x) = sim(x + t)
    sim = cv2.warpAffine(ref, M_, (320, 256), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    al = estimate_alignment(sim, ref, motion="affine", levels=3)
    # ref(x) ~ sim(M x) with M a translation by (+3.4, -2.2)
    assert abs(al.matrix[0, 2] - 3.4) < 0.25 and abs(al.matrix[1, 2] - (-2.2)) < 0.25
    assert al.residual_px < 0.5
    img = torch.rand(1, 3, 20, 30)
    assert torch.equal(Alignment().warp(img), img)
    # warp with a pure translation moves content by that many pixels
    t = Alignment(np.array([[1, 0, 2.0], [0, 1, 0], [0, 0, 1]]))
    w = t.warp(img)
    assert torch.allclose(w[..., 3:-3], img[..., 5:-1][..., :w.shape[-1] - 6], atol=1e-5) or torch.allclose(w[..., :20], img[..., 2:22], atol=1e-5)
    # scaling to another grid keeps the mean
    up = Alignment(out_hw=(40, 60)).warp(img)
    assert up.shape[-2:] == (40, 60) and abs(float(up.mean() - img.mean())) < 0.03


def test_metrics_basic():
    a = torch.randint(0, 256, (3, 40, 50), dtype=torch.uint8)
    assert M.psnr(a, a) == float("inf") and M.ssim(a, a) == pytest.approx(1.0, abs=1e-5)
    ev = M.ev_error(a, a)
    assert ev["mean"] == [0, 0, 0]
    # a one-stop brightness change in linear light measures +1 EV
    lin = torch.full((3, 30, 30), 0.1)
    enc = lambda l: (255 * torch.where(l <= 0.0031308, 12.92 * l, 1.055 * l ** (1 / 2.4) - 0.055)).round().to(torch.uint8)
    assert M.ev_error(enc(lin * 2), enc(lin))["mean"][0] == pytest.approx(1.0, abs=0.03)
    assert M.emd_per_channel(a, a) == [0, 0, 0]
    flat0, flat10 = torch.zeros(3, 8, 8, dtype=torch.uint8), torch.full((3, 8, 8), 10, dtype=torch.uint8)
    assert M.emd_per_channel(flat0, flat10) == pytest.approx([10, 10, 10])
    assert M.psnr(flat0, flat10) == pytest.approx(10 * np.log10(255 ** 2 / 100))


def test_neural3a_reward_terms_match_reference_formulas():
    import cv2
    rng = np.random.default_rng(0)
    img = (rng.random((200, 220, 3)) * 255).astype(np.uint8)
    t = torch.from_numpy(img).permute(2, 0, 1)
    r = M.reward_terms(t)
    y = M.luma01(M.center_crop(t))
    assert y.shape == (150, 150)
    assert r["midtone"] == pytest.approx(1 - float((y - 0.5).abs().mean()), rel=1e-6)
    # tenengrad: 9x9 Sobel via OpenCV on the same crop (valid region), /1e5
    yc = y.numpy().astype(np.float32)
    gx = cv2.Sobel(yc, cv2.CV_32F, 1, 0, ksize=9)[4:-4, 4:-4]
    gy = cv2.Sobel(yc, cv2.CV_32F, 0, 1, ksize=9)[4:-4, 4:-4]
    assert r["tenengrad_raw"] == pytest.approx(float((gx ** 2 + gy ** 2).mean()) / 1e5, rel=1e-3)
    s = r["sobel_raw"]
    assert r["sobel"] == pytest.approx(2 * s / (s + 5.0))
    assert r["tenengrad"] == pytest.approx(2 * r["tenengrad_raw"] / (r["tenengrad_raw"] + 25.0))
    # flat image: no edges
    flat = torch.full((3, 160, 160), 128, dtype=torch.uint8)
    assert M.reward_terms(flat)["sobel_raw"] < 1e-5


def test_noise_level_function_tracks_added_noise():
    g = torch.Generator().manual_seed(0)
    base = torch.full((3, 128, 128), 100.0)
    noisy = (base + torch.randn(base.shape, generator=g) * 4).round().clamp(0, 255)
    c, v = M.noise_level_function(noisy)
    assert len(v) >= 1 and abs(np.median(v) / (16.0 * (0.299 ** 2 + 0.587 ** 2 + 0.114 ** 2)) - 1) < 0.1     # luma weights


def test_residual_net_is_identity_when_untrained_and_trains():
    from camsim.isp.residual import ResidualNet, train_residual
    net = ResidualNet(8)
    x = torch.rand(2, 3, 16, 16)
    assert torch.allclose(net(x, [100, 400], [0.01, 0.1]), x, atol=1e-6)
    sim = torch.rand(3, 32, 32) * 0.5
    tgt = (sim * 1.2).clamp(0, 1)
    hist = train_residual(net, [(sim, tgt, 100, 0.01)], epochs=60, lr=5e-3, crop=32)
    assert hist[-1] < hist[0]
