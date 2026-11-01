"""OPTIONAL learned residual (off by default).

A small CNN that corrects what the global ISP model cannot express (local / adaptive tone
mapping, residual colour error). Input: the gamma-domain RGB (0..1) produced by the ISP model plus
two constant channels, log2(ISO) and log2(t) normalised to [-1, 1] (so the correction can depend on
the capture settings). Output: input + delta. The last layer is zero-initialised, so an untrained
network is the identity. Train on calibration pairs from scenes other than the evaluation scene
and validate leave-one-scene-out (``scripts/fit_calibration.py --residual``).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import torch
import torch.nn as nn


def _norm_settings(iso, exposure, device):
    li = (torch.log2(torch.as_tensor(iso, dtype=torch.float32, device=device)) - math.log2(50)) / (math.log2(1600) - math.log2(50)) * 2 - 1
    lt = (torch.log2(torch.as_tensor(exposure, dtype=torch.float32, device=device)) - math.log2(1e-3)) / (math.log2(0.1) - math.log2(1e-3)) * 2 - 1
    return li, lt


class ResidualNet(nn.Module):
    def __init__(self, width: int = 24):
        super().__init__()
        self.body = nn.Sequential(
            nn.Conv2d(5, width, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv2d(width, width, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv2d(width, 3, 3, padding=1))
        nn.init.zeros_(self.body[-1].weight)
        nn.init.zeros_(self.body[-1].bias)

    def forward(self, x: torch.Tensor, iso: Optional[Sequence[float]] = None, exposure: Optional[Sequence[float]] = None):
        B, _, H, W = x.shape
        if iso is None or exposure is None:
            li = lt = torch.zeros(B, device=x.device)
        else:
            li, lt = _norm_settings(iso, exposure, x.device)
        cond = torch.stack([li, lt], 1).view(B, 2, 1, 1).expand(B, 2, H, W)
        return (x + self.body(torch.cat([x, cond], 1))).clamp(0, 1)


def train_residual(net: ResidualNet, samples, epochs: int = 200, lr: float = 1e-3, device="cpu", crop: int = 128):
    """``samples``: iterable of (sim_gamma01 (3,H,W), target01 (3,H,W), iso, exposure). Returns loss history."""
    net.to(device).train()
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    hist = []
    samples = list(samples)
    for ep in range(epochs):
        tot = 0.0
        for sim, tgt, iso, t in samples:
            _, H, W = sim.shape
            y0 = torch.randint(0, max(H - crop, 1), (1,)).item()
            x0 = torch.randint(0, max(W - crop, 1), (1,)).item()
            s = sim[None, :, y0:y0 + crop, x0:x0 + crop].to(device)
            g = tgt[None, :, y0:y0 + crop, x0:x0 + crop].to(device)
            loss = torch.nn.functional.l1_loss(net(s, [iso], [t]), g)
            opt.zero_grad(); loss.backward(); opt.step()
            tot += float(loss)
        hist.append(tot / max(len(samples), 1))
    net.eval()
    return hist
