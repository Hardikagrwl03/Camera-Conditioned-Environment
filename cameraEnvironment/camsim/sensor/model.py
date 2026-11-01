"""Sensor forward model (torch, batched, CPU or GPU).

Per Bayer pixel, in electrons ``e`` and DN (above black)::

    e0   = (S0 - dark(t0)) / g(ISO0)          source signal electrons at (ISO0, t0)
    e    = Binomial(round(e0), t/t0)          single-frame source (exact Poisson thinning)
         | Poisson(e0 * t/t0)                 averaged / merged source
    e   += Poisson(dark_rate * t)             target dark current (+ DSNU / hot pixel map)
    D    = g_analog(ISO) * e + read noise     read noise Gaussian or Tukey-lambda, + row noise
    D    = round(D) [* digital gain, round]   ADC quantisation (+ digital gain gaps above analog max)
    raw  = clip(D + black, 0, white)

The read noise of a single-frame source is partly baked into ``e0`` and, after thinning, survives
with variance ``p^2 * sigma_src^2`` (p = t/t0). It is *subtracted from the variance budget*: only
``max(sigma_target^2 - p^2 * sigma_src^2, 0)`` is added. The residual error occurs only where the
baked source noise exceeds the target's read noise (documented in the README).
"""
from __future__ import annotations

import json
import math
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
import torch

DEFAULT_GAIN_DN_PER_E = 0.2     # at ISO 50, 10-bit; only used when nothing better is known
DEFAULT_READ_NOISE_E = 3.0


@dataclass
class SensorParams:
    iso_ref: float = 50.0
    gain_ref: float = DEFAULT_GAIN_DN_PER_E          # DN per electron at iso_ref
    gain_table: Optional[List[Tuple[float, float]]] = None   # [(iso, DN/e)] overrides the linear law
    analog_iso_max: Optional[float] = None          # above this ISO gain is digital (quantisation gaps)
    read_noise_e: float = DEFAULT_READ_NOISE_E      # input-referred rms electrons (used if no table)
    read_noise_table: Optional[List[Tuple[float, float]]] = None   # [(iso, rms final DN)]
    read_dist: str = "gaussian"                     # "gaussian" | "tukey"
    tukey_lambda: float = 0.14                      # ~gaussian; smaller = heavier tails (ELD)
    row_noise_e: float = 0.0                        # row noise rms, input-referred electrons
    dark_rate_e_s: float = 0.0                      # mean dark current, electrons / second
    dsnu_hot_threshold_sigma: float = 6.0
    source: str = "default"                         # "calibration" | "noiseprofile" | "default"
    notes: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        for k in ("gain_table", "read_noise_table"):
            if d.get(k) is not None:
                d[k] = [tuple(x) for x in d[k]]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    # -------------------------------------------------------------- constructors
    @classmethod
    def default(cls) -> "SensorParams":
        p = cls(source="default")
        p.notes.append("DEFAULT sensor parameters (no calibration, no NoiseProfile): "
                       f"gain {DEFAULT_GAIN_DN_PER_E} DN/e at ISO 50, read noise {DEFAULT_READ_NOISE_E} e-. "
                       "Noise levels are NOT trustworthy.")
        return p

    @classmethod
    def from_noise_profile(cls, frame) -> "SensorParams":
        """Derive gain and read noise from the DNG NoiseProfile of the source frame.

        NoiseProfile (S, O) models var(x) = S x + O for x normalised to [0,1] above black, so
        gain g = S * range (DN/e) and read variance = O * range^2 (DN^2).
        """
        np_ = frame.noise_profile
        if np_ is None or len(np_) == 0 or not np.all(np.isfinite(np_)):
            return cls.default()
        S = float(np.mean(np_[:, 0]))
        O = float(np.mean(np_[:, 1]))
        rng = frame.range_dn
        g = S * rng
        read_dn = math.sqrt(max(O, 0.0)) * rng
        if not (1e-3 < g < 20):
            warnings.warn(f"implausible gain {g} DN/e from NoiseProfile; using defaults")
            return cls.default()
        p = cls(iso_ref=frame.iso, gain_ref=g, read_noise_e=read_dn / g, source="noiseprofile")
        p.notes.append(f"gain {g:.4f} DN/e and read noise {read_dn:.3f} DN at ISO {frame.iso:g} from DNG NoiseProfile; "
                       "gain assumed proportional to ISO, read noise constant in electrons")
        return p


def _interp_log(table: Sequence[Tuple[float, float]], iso: float) -> float:
    xs = np.log2([t[0] for t in table])
    ys = np.log([t[1] for t in table])
    return float(np.exp(np.interp(math.log2(iso), xs, ys)))   # clamps at the ends


def tukey_lambda_var(lam: float) -> float:
    """Variance of the standard Tukey-lambda distribution Q(u) = (u^l - (1-u)^l)/l."""
    if abs(lam) < 1e-9:
        return math.pi ** 2 / 3          # logistic
    return (2.0 / lam ** 2) * (1.0 / (1 + 2 * lam) - math.gamma(lam + 1) ** 2 / math.gamma(2 * lam + 2))


def sample_read_noise(shape, sigma, dist: str, lam: float, device, generator) -> torch.Tensor:
    """Zero-mean noise with per-element std ``sigma`` (tensor broadcastable to shape)."""
    if dist == "gaussian":
        z = torch.randn(shape, device=device, generator=generator)
    elif dist == "tukey":
        u = torch.rand(shape, device=device, generator=generator).clamp_(1e-7, 1 - 1e-7)
        if abs(lam) < 1e-9:
            z = torch.log(u / (1 - u))
        else:
            z = (u ** lam - (1 - u) ** lam) / lam
        z = z / math.sqrt(tukey_lambda_var(lam))
    else:
        raise ValueError(f"unknown read noise distribution {dist}")
    return z * sigma


class SensorModel:
    def __init__(self, params: SensorParams, device="cpu", dsnu_e_s: Optional[np.ndarray] = None):
        self.params = params
        self.device = torch.device(device)
        self.dsnu_e_s = None if dsnu_e_s is None else torch.as_tensor(dsnu_e_s, dtype=torch.float32)

    # ------------------------------------------------------------------ laws
    def gain(self, iso: float) -> float:
        """Total DN per electron at ``iso`` (analog x digital)."""
        p = self.params
        if p.gain_table:
            return _interp_log(p.gain_table, iso) if len(p.gain_table) > 1 else p.gain_table[0][1] * iso / p.gain_table[0][0]
        return p.gain_ref * iso / p.iso_ref

    def analog_gain(self, iso: float) -> Tuple[float, float]:
        """(analog DN/e, digital multiplier)."""
        p = self.params
        if p.analog_iso_max is None or iso <= p.analog_iso_max:
            return self.gain(iso), 1.0
        ga = self.gain(p.analog_iso_max)
        return ga, self.gain(iso) / ga

    def read_sigma_e(self, iso: float) -> float:
        """Input-referred read noise (electrons rms) at ``iso``."""
        p = self.params
        if p.read_noise_table:
            return _interp_log(p.read_noise_table, iso) / self.gain(iso)
        return p.read_noise_e

    def read_sigma_dn(self, iso: float) -> float:
        return self.read_sigma_e(iso) * self.gain(iso)

    # ------------------------------------------------------------------ simulation
    @torch.no_grad()
    def simulate(self, scene, isos: Sequence[float], exposures: Sequence[float], noisy: bool = True,
                 generator: Optional[torch.Generator] = None):
        """Render raw frames for a batch of (iso, exposure). Returns (raw (B,H,W) float32, clip (B,H,W) bool).

        ``scene`` provides: ``signal`` (H,W) DN above black at (iso0, t0) *including* any bracket
        extrapolation, ``clip_mask`` (H,W) bool, ``black_map`` (H,W), ``white``, ``iso0``, ``t0``,
        ``n_eff`` (effective number of averaged frames), ``thinning`` ("binomial" | "poisson").
        """
        p = self.params
        dev = self.device
        B = len(isos)
        t0, iso0 = scene.t0, scene.iso0
        for t in exposures:
            if t > t0 * (1 + 5e-3):
                raise ValueError(f"exposure {t} s > source exposure {t0} s")
        S0 = torch.as_tensor(scene.signal, dtype=torch.float32, device=dev)
        H, W = S0.shape
        black = torch.as_tensor(scene.black_map, dtype=torch.float32, device=dev)
        clip_src = torch.as_tensor(scene.clip_mask, device=dev)
        g0 = self.gain(iso0)

        dark_rate = float(p.dark_rate_e_s)
        rate = None
        if self.dsnu_e_s is not None:
            if tuple(self.dsnu_e_s.shape) == (H, W):
                rate = self.dsnu_e_s.to(dev).view(1, H, W) + dark_rate
            else:
                warnings.warn("DSNU map shape differs from the frame; ignoring it")
        if rate is None and dark_rate > 0:
            rate = torch.full((1, 1, 1), dark_rate, device=dev)
        dark0 = rate[0] * t0 if rate is not None else 0.0
        e0 = (S0 / g0 - dark0).clamp_min(0.0)                                                # (H,W)

        pt = torch.tensor([min(t / t0, 1.0) for t in exposures], dtype=torch.float32, device=dev).view(B, 1, 1)
        g_tot = torch.tensor([self.gain(i) for i in isos], dtype=torch.float32, device=dev).view(B, 1, 1)
        ga = torch.tensor([self.analog_gain(i)[0] for i in isos], dtype=torch.float32, device=dev).view(B, 1, 1)
        gd = torch.tensor([self.analog_gain(i)[1] for i in isos], dtype=torch.float32, device=dev).view(B, 1, 1)
        tt = torch.tensor(list(exposures), dtype=torch.float32, device=dev).view(B, 1, 1)
        rate_t = rate * tt if rate is not None else None

        # ---- noise-free mean
        mu = e0.view(1, H, W) * pt
        if rate_t is not None:
            mu = mu + rate_t
        if not noisy:
            D = g_tot * mu
            raw = (D + black.view(1, H, W)).clamp(0.0, float(scene.white))
            clip = clip_src.view(1, H, W) | (raw >= scene.white)
            return raw, clip.expand(B, H, W).contiguous()

        # ---- photon shot noise
        if scene.thinning == "binomial":
            n = torch.round(e0).view(1, H, W).expand(B, H, W).contiguous()
            prob = pt.expand(B, H, W).contiguous()
            e = torch.binomial(n, prob, generator=generator)
        else:
            e = torch.poisson((e0.view(1, H, W) * pt).expand(B, H, W).contiguous(), generator=generator)
        if rate_t is not None:
            e = e + torch.poisson(rate_t.expand(B, H, W).contiguous(), generator=generator)

        # ---- read + row noise, with the source's baked noise subtracted from the budget
        sig_src_e = self.read_sigma_e(iso0) / math.sqrt(max(scene.n_eff, 1.0))
        sig_t_e = torch.tensor([self.read_sigma_e(i) for i in isos], dtype=torch.float32, device=dev).view(B, 1, 1)
        add_var = (sig_t_e ** 2 - pt ** 2 * sig_src_e ** 2).clamp_min(0.0)
        sigma_add_dn = torch.sqrt(add_var) * ga
        D = ga * e + sample_read_noise((B, H, W), sigma_add_dn, p.read_dist, p.tukey_lambda, dev, generator)
        if p.row_noise_e > 0:
            row_var = max(p.row_noise_e ** 2, 0.0) * torch.ones_like(pt)
            row_var = (row_var - pt ** 2 * (p.row_noise_e ** 2 / max(scene.n_eff, 1.0))).clamp_min(0.0)
            rn = torch.randn((B, H, 1), device=dev, generator=generator) * torch.sqrt(row_var) * ga
            D = D + rn

        # ---- ADC quantisation, digital gain, black level, clipping
        D = torch.round(D)
        if bool((gd != 1.0).any()):
            D = torch.round(D * gd)
        raw = (D + black.view(1, H, W)).clamp(0.0, float(scene.white))
        clip = clip_src.view(1, H, W) | (raw >= scene.white)
        return raw, clip.expand(B, H, W).contiguous()
