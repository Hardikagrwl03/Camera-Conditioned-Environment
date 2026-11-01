#!/usr/bin/env python
"""Render every sweep grid point (at the raw frame's focus) -> outputs/<sweep>/sim_png, sim_raw, manifest."""
import argparse
from pathlib import Path

from _common import REPO, add_common_args, build_sim


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    add_common_args(ap)
    ap.add_argument("--no-raw", action="store_true", help="do not write 16-bit TIFF raws")
    ap.add_argument("--dense", nargs=2, type=int, metavar=("N_ISO", "N_EXP"), default=None,
                    help="instead of the sweep grid, render a log-uniform N_ISO x N_EXP grid")
    ap.add_argument("--noise-free", action="store_true", help="render the noise-free mean")
    args = ap.parse_args(argv)

    from camsim.sweepout import dense_grid, simulate_sweep
    sim = build_sim(args)
    for n in sim.notes:
        print("note:", n)
    pts = dense_grid(sim, *args.dense) if args.dense else None
    out = Path(args.outputs) / args.sweep
    res = simulate_sweep(sim, out, seed=args.seed, points=pts, write_raw=not args.no_raw, chunk=args.chunk,
                         noisy=not args.noise_free)
    print(f"wrote {res['n']} frames to {res['dir']}")


if __name__ == "__main__":
    main()
