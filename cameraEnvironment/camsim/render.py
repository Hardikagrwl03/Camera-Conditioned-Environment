"""RawSimulator: (SceneRadiance, SensorModel) -> simulated linear raw at (iso, t), batched."""
from __future__ import annotations

from typing import Iterator, Optional, Sequence, Tuple

import numpy as np
import torch

from . import params as P
from .sensor.model import SensorModel
from .sensor.source import SceneRadiance


def auto_chunk(h: int, w: int, device: torch.device, max_bytes: Optional[int] = None,
               bytes_per_pixel: int = 96) -> int:
    """Batch elements per chunk so peak memory stays under ``max_bytes`` (default: 40 % of free GPU
    memory, or 4 GB on CPU). ``bytes_per_pixel`` covers raw sim + demosaic + RGB intermediates."""
    if max_bytes is None:
        if device.type == "cuda":
            free, _ = torch.cuda.mem_get_info(device)
            max_bytes = int(0.4 * free)
        else:
            max_bytes = 4 << 30
    return max(1, int(max_bytes // (h * w * bytes_per_pixel)))


class RawSimulator:
    def __init__(self, scene: SceneRadiance, sensor: SensorModel, iso_range=P.ISO_RANGE,
                 exposure_range=P.EXPOSURE_RANGE_S):
        self.scene = scene
        self.sensor = sensor
        self.iso_range = iso_range
        self.exposure_range = exposure_range

    def check(self, isos, exposures):
        for i in isos:
            P.validate_iso(i, self.iso_range)
        for t in exposures:
            P.validate_exposure(t, self.scene.t0, self.exposure_range)

    def generator(self, seed: int) -> torch.Generator:
        g = torch.Generator(device=self.sensor.device)
        g.manual_seed(int(seed))
        return g

    def render(self, isos: Sequence[float], exposures: Sequence[float], noisy: bool = True,
               seed: int = 0, chunk: Optional[int] = None) -> Iterator[Tuple[slice, torch.Tensor, torch.Tensor]]:
        """Yield ``(slice, raw (b,H,W), clip (b,H,W))`` chunks. Deterministic for a given seed and
        chunking."""
        isos, exposures = list(map(float, isos)), list(map(float, exposures))
        if len(isos) != len(exposures):
            raise ValueError("isos and exposures must have the same length")
        self.check(isos, exposures)
        h, w = self.scene.shape
        chunk = chunk or auto_chunk(h, w, self.sensor.device, bytes_per_pixel=32)
        gen = self.generator(seed)
        for s in range(0, len(isos), chunk):
            sl = slice(s, s + chunk)
            raw, clip = self.sensor.simulate(self.scene, isos[sl], exposures[sl], noisy=noisy, generator=gen)
            yield sl, raw, clip

    def render_all(self, isos, exposures, noisy=True, seed=0, chunk=None):
        raws, clips = [], []
        for _, r, c in self.render(isos, exposures, noisy, seed, chunk):
            raws.append(r)
            clips.append(c)
        return torch.cat(raws), torch.cat(clips)
