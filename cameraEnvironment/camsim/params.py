"""Log-scale helpers and range validation.

Both camera axes (ISO and exposure time) are treated on a log scale everywhere: sampling, grids,
plots and error reporting. ``ev`` is log2, i.e. photographic stops.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

import numpy as np

ISO_RANGE = (50.0, 1600.0)
EXPOSURE_RANGE_S = (1e-3, 100e-3)

#: Relative tolerance when comparing an actual exposure to the range ends. Sensors quantise the
#: exposure to a line-time step, so 100 ms is reported as e.g. 100.003 ms.
EXPOSURE_REL_TOL = 5e-3


def ev(x):
    """log2 of a scalar / array (stops)."""
    if isinstance(x, (int, float)):
        return math.log2(x)
    return np.log2(x)


def log_grid(lo: float, hi: float, n: int) -> np.ndarray:
    """``n`` values log-uniformly spaced in [lo, hi], inclusive."""
    if n < 1:
        raise ValueError("n must be >= 1")
    if n == 1:
        return np.array([math.sqrt(lo * hi)])
    return np.exp(np.linspace(math.log(lo), math.log(hi), n))


def sample_log_uniform(lo: float, hi: float, n: int, seed: Optional[int] = None) -> np.ndarray:
    """``n`` samples whose log2 is uniform on [log2 lo, log2 hi]."""
    rng = np.random.default_rng(seed)
    return 2.0 ** rng.uniform(math.log2(lo), math.log2(hi), size=n)


def validate_iso(iso: float, iso_range=ISO_RANGE) -> float:
    iso = float(iso)
    if not (iso_range[0] <= iso <= iso_range[1]):
        raise ValueError(f"ISO {iso} outside supported range {iso_range}")
    return iso


def validate_exposure(t: float, t_source: Optional[float] = None, exposure_range=EXPOSURE_RANGE_S) -> float:
    """Validate an exposure time in seconds.

    Exposures longer than the source exposure are rejected: the source holds no photons beyond
    ``t_source`` so they cannot be synthesised (see README, "Why the physics works in one
    direction"). A relative tolerance absorbs sensor line-time quantisation.
    """
    t = float(t)
    lo, hi = exposure_range
    if t < lo * (1 - EXPOSURE_REL_TOL) or t > hi * (1 + EXPOSURE_REL_TOL):
        raise ValueError(f"exposure {t * 1e3:.4g} ms outside supported range {lo * 1e3:g}-{hi * 1e3:g} ms")
    if t_source is not None and t > t_source * (1 + EXPOSURE_REL_TOL):
        raise ValueError(
            f"exposure {t * 1e3:.4g} ms exceeds the source exposure {t_source * 1e3:.4g} ms; "
            "the simulator can only thin photons, never add them")
    return t


def in_range(iso: float, t: float, iso_range=ISO_RANGE, exposure_range=EXPOSURE_RANGE_S) -> bool:
    return (iso_range[0] <= iso <= iso_range[1]
            and exposure_range[0] * (1 - EXPOSURE_REL_TOL) <= t <= exposure_range[1] * (1 + EXPOSURE_REL_TOL))


def as_list(x) -> list:
    if isinstance(x, (int, float)):
        return [float(x)]
    return [float(v) for v in x]
