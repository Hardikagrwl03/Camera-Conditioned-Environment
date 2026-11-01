"""Exact integer replicas of YuvConverter.kt and Downscaler.kt (golden vectors + literal scalar ports)."""
import numpy as np
import torch

from camsim.isp.pipeline import box_downscale_int, yuv420_to_rgb_int


def kotlin_shr(x, n):
    return x >> n          # Python ints: arithmetic shift == Kotlin `shr` on Int


def kotlin_yuv_pixel(y, u, v):
    """Literal transcription of the inner loop of yuv420ToArgb (after plane indexing)."""
    y = y - 16
    u = u - 128
    v = v - 128
    y1192 = 1192 * (0 if y < 0 else y)
    r = kotlin_shr(y1192 + 1634 * v, 10)
    g = kotlin_shr(y1192 - 833 * v - 400 * u, 10)
    b = kotlin_shr(y1192 + 2066 * u, 10)
    clamp = lambda c: 0 if c < 0 else 255 if c > 255 else c
    return clamp(r), clamp(g), clamp(b)


def test_yuv_golden_vectors_hand_computed():
    # (Y, U, V) -> (R, G, B), computed by hand from the Kotlin integer formulas
    cases = {
        (16, 128, 128): (0, 0, 0),            # black: y=0
        (235, 128, 128): (254, 254, 254),     # 1192*219 = 261048; >>10 = 254
        (128, 128, 128): (132, 132, 132),     # 1192*112 = 133504 >> 10 = 130.37 -> 130 ; see below
        (81, 90, 240): (254, 0, 0),           # BT.601 red: r=260488>>10=254, g=-616>>10=-1->0, b=-1028>>10=-2->0
        (0, 128, 128): (0, 0, 0),             # y < 0 clamps to 0
    }
    cases[(128, 128, 128)] = (130, 130, 130)
    for (y, u, v), rgb in cases.items():
        assert kotlin_yuv_pixel(y, u, v) == rgb, (y, u, v)
        out = yuv420_to_rgb_int(torch.tensor([[[y, y], [y, y]]]), torch.tensor([[[u]]]), torch.tensor([[[v]]]))
        assert tuple(int(c) for c in out[0, :, 0, 0]) == rgb


def test_yuv_torch_matches_scalar_port_with_nearest_chroma_and_odd_sizes():
    rng = np.random.default_rng(0)
    H, W = 7, 9                                       # odd sizes: chroma is (4, 5), nearest `shr 1`
    y = rng.integers(0, 256, (1, H, W))
    u = rng.integers(0, 256, (1, 4, 5))
    v = rng.integers(0, 256, (1, 4, 5))
    out = yuv420_to_rgb_int(torch.from_numpy(y), torch.from_numpy(u), torch.from_numpy(v))[0].numpy()
    for r in range(H):
        for c in range(W):
            assert tuple(out[:, r, c]) == kotlin_yuv_pixel(int(y[0, r, c]), int(u[0, r >> 1, c >> 1]), int(v[0, r >> 1, c >> 1]))


def test_negative_arithmetic_shift():
    assert int(torch.bitwise_right_shift(torch.tensor([-616], dtype=torch.int32), 10)) == -1
    assert kotlin_shr(-616, 10) == -1


def kotlin_box(pixels, W, H, f):
    """Literal port of boxDownscale for one channel set. pixels[y][x] = (r, g, b)."""
    oh, ow = H // f, W // f
    n = f * f
    out = np.zeros((oh, ow, 3), np.int64)
    for oy in range(oh):
        for ox in range(ow):
            s = [0, 0, 0]
            for dy in range(f):
                for dx in range(f):
                    p = pixels[oy * f + dy][ox * f + dx]
                    for k in range(3):
                        s[k] += int(p[k])
            half = n // 2
            out[oy, ox] = [(s[k] + half) // n for k in range(3)]
    return out


def test_box_downscale_matches_scalar_port_rounding_and_crop():
    rng = np.random.default_rng(1)
    for (H, W, f) in [(6, 8, 2), (7, 11, 3), (9, 9, 4)]:           # remainder rows/cols are cropped
        img = rng.integers(0, 256, (H, W, 3))
        ref = kotlin_box(img, W, H, f)
        out = box_downscale_int(torch.from_numpy(img).permute(2, 0, 1)[None].to(torch.int32), f)[0].permute(1, 2, 0).numpy()
        assert out.shape == ref.shape and (out == ref).all()
    # rounding half up: (1+2+2+2+half 2)/4 : sum 7 -> (7+2)//4 = 2 ; sum 6 -> 2 ; sum 5 -> 1
    x = torch.tensor([[[[1, 2], [2, 2]]]], dtype=torch.int32).repeat(1, 3, 1, 1)
    assert int(box_downscale_int(x, 2)[0, 0, 0, 0]) == 2
    x = torch.tensor([[[[1, 1], [1, 2]]]], dtype=torch.int32).repeat(1, 3, 1, 1)
    assert int(box_downscale_int(x, 2)[0, 0, 0, 0]) == 1
    assert box_downscale_int(x, 1) is x
