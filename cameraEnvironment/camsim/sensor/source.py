"""SceneRadiance: the best estimate of the scene signal at the source exposure.

Modes (recorded in ``mode`` / ``notes``):

``single``   one DNG as is. Thinning is exact Binomial; the frame's own read noise is baked in.
``burst``    mean of N frames at the same setting (read noise / sqrt(N)); thinning is Poisson of
             the mean.
``bracket``  inverse-variance HDR merge in linear DN/s of the burst/single frame and a raw
             exposure bracket; recovers source-clipped highlights. Poisson of the merged mean.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from ..io import sidecar as sc
from ..io.dng import RawFrame, load_dng
from .model import SensorModel, SensorParams

CLIP_MARGIN_FRAC = 0.005   # pixels within 0.5 % of range of the white level count as clipped


@dataclass
class SceneRadiance:
    signal: np.ndarray            # (H,W) DN above black at (iso0, t0); may exceed the range (bracket)
    clip_mask: np.ndarray         # (H,W) bool: unrecoverable highlights
    black_map: np.ndarray         # (H,W)
    white: float
    iso0: float
    t0: float
    cfa: str
    mode: str = "single"
    n_eff: float = 1.0
    frame: Optional[RawFrame] = None
    notes: List[str] = field(default_factory=list)

    @property
    def thinning(self) -> str:
        return "binomial" if self.mode == "single" else "poisson"

    @property
    def shape(self):
        return self.signal.shape

    @property
    def clipped_fraction(self) -> float:
        return float(self.clip_mask.mean())

    @classmethod
    def from_frame(cls, frame: RawFrame) -> "SceneRadiance":
        black = frame.black_map()
        S = frame.bayer - black
        margin = CLIP_MARGIN_FRAC * frame.range_dn
        clip = frame.bayer >= frame.white_level - margin
        notes = []
        if clip.mean() > 0:
            notes.append(f"{100 * clip.mean():.3f} % of source pixels are clipped (masked; unrecoverable without a bracket)")
        return cls(S.astype(np.float32), clip, black, frame.white_level, frame.iso, frame.exposure_s,
                   frame.cfa, "single", 1.0, frame, notes)


def merge_burst(frames: Sequence[RawFrame]) -> SceneRadiance:
    base = SceneRadiance.from_frame(frames[0])
    if len(frames) == 1:
        return base
    stack = np.stack([f.bayer for f in frames]).astype(np.float64)
    mean = stack.mean(0)
    clip = (stack >= frames[0].white_level - CLIP_MARGIN_FRAC * frames[0].range_dn).any(0)
    base.signal = (mean - base.black_map).astype(np.float32)
    base.clip_mask = clip
    base.mode = "burst"
    base.n_eff = float(len(frames))
    base.notes = [f"burst of {len(frames)} frames averaged (read noise / {np.sqrt(len(frames)):.2f})"]
    if clip.mean() > 0:
        base.notes.append(f"{100 * clip.mean():.3f} % of pixels clipped in at least one burst frame")
    return base


def merge_bracket(base: SceneRadiance, brackets: Sequence[RawFrame], sensor: SensorModel) -> SceneRadiance:
    """Inverse-variance merge in linear DN/s; each frame contributes only where unclipped.

    A bracket frame is a single capture at ISO0 and shorter exposure ``t_k``. Its rate estimate is
    ``S_k / t_k`` with variance ``(g S_k + sigma^2) / t_k^2`` (shot + read, DN^2). Frames are
    grouped by exposure and averaged first.
    """
    iso0, t0 = base.iso0, base.t0
    g = sensor.gain(iso0)
    var_read = sensor.read_sigma_dn(iso0) ** 2
    by_t = {}
    for f in brackets:
        if abs(f.iso - iso0) > 1e-6:
            warnings.warn(f"bracket frame {f.path} at ISO {f.iso}, expected {iso0}; skipped")
            continue
        by_t.setdefault(round(f.exposure_s, 7), []).append(f)

    black = base.black_map
    num = np.zeros_like(base.signal, np.float64)
    den = np.zeros_like(base.signal, np.float64)

    def add(S, t, n, valid):
        var = (g * np.maximum(S, 0) + var_read) / (t * t) / n
        w = np.where(valid, 1.0 / np.maximum(var, 1e-12), 0.0)
        nonlocal num, den
        num += w * (S / t)
        den += w

    valid0 = ~base.clip_mask
    add(base.signal.astype(np.float64), t0, base.n_eff, valid0)
    shortest_valid = np.zeros_like(valid0)
    ratios = []
    for t, fs in sorted(by_t.items()):
        n = len(fs)
        stack = np.stack([f.bayer for f in fs]).astype(np.float64)
        S = stack.mean(0) - black
        valid = ~(stack >= fs[0].white_level - CLIP_MARGIN_FRAC * fs[0].range_dn).any(0)
        add(S, t, n, valid)
        shortest_valid |= valid
        both = valid & valid0 & (base.signal > 0.05 * base.white)
        if both.any():
            ratios.append((t, float(np.median((S[both] / t) / (base.signal[both] / t0)))))
    rate = np.where(den > 0, num / np.maximum(den, 1e-30), base.signal / t0)
    out = SceneRadiance(
        signal=(rate * t0).astype(np.float32), clip_mask=~(valid0 | shortest_valid),
        black_map=black, white=base.white, iso0=iso0, t0=t0, cfa=base.cfa, mode="bracket",
        n_eff=max(base.n_eff, 1.0), frame=base.frame, notes=list(base.notes))
    out.notes.append(f"HDR bracket merge of {sum(len(v) for v in by_t.values())} frames at "
                     f"{len(by_t)} exposures; {100 * out.clip_mask.mean():.3f} % remain clipped")
    for t, r in ratios:
        out.notes.append(f"bracket linearity check t={t * 1e3:.3g} ms: median rate ratio vs source {r:.4f}")
        if abs(r - 1) > 0.03:
            warnings.warn(f"bracket frame at {t * 1e3:.3g} ms disagrees with the source rate by "
                          f"{100 * (r - 1):.1f} %: exposure non-linearity or scene change")
    return out


def load_scene(raw_dir, sensor: Optional[SensorModel] = None, iso0: float = 50.0,
               focus_d: Optional[float] = None, prefer: str = "auto") -> SceneRadiance:
    """Build the SceneRadiance from a ``rawframes/<sweep>/`` folder.

    Works with a single DNG alone; uses burst (tag ``raw``) and bracket (tag ``brk``) frames when
    ``capture_manifest.json`` lists them.
    """
    raw_dir = Path(raw_dir)
    man = sc.CaptureManifest.load(raw_dir)
    raws = man.by_tag("raw")
    if not raws:
        raise FileNotFoundError(f"no scene DNG (tag 'raw') in {raw_dir}")
    if focus_d is not None:
        sel = [e for e in raws if e.focus_d is None or abs(e.focus_d - focus_d) < 0.02]
        raws = sel or raws
    # canonical = highest exposure at lowest ISO (ISO 50 / 100 ms); frame 0 first
    key = lambda e: (e.iso or 0, -(e.exposure_s or 0), e.frame)
    ref = min(raws, key=key)
    group = [e for e in raws if e.iso == ref.iso and e.exposure_s == ref.exposure_s]
    group.sort(key=lambda e: e.frame)
    frames = [load_dng(man.path(e.file), sidecar=man.sidecar(e), prefer=prefer) for e in group]
    scene = merge_burst(frames)
    brks = man.by_tag("brk")
    if brks:
        if sensor is None:
            sensor = SensorModel(SensorParams.from_noise_profile(frames[0]))
        bf = [load_dng(man.path(e.file), sidecar=man.sidecar(e), prefer=prefer) for e in brks]
        scene = merge_bracket(scene, bf, sensor)
    scene.notes.insert(0, f"source: {len(frames)} frame(s) at ISO {frames[0].iso:g}, "
                          f"{frames[0].exposure_s * 1e3:g} ms, mode={scene.mode}")
    return scene
