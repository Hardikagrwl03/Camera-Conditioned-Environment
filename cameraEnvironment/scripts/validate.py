#!/usr/bin/env python
"""Compare simulated vs real sweep frames; writes metrics.csv, summary.json, report.html."""
import argparse
from pathlib import Path

from _common import REPO, add_common_args, build_sim


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    add_common_args(ap)
    ap.add_argument("-K", type=int, default=4, help="noisy realisations per grid point")
    ap.add_argument("--max-points", type=int, default=None)
    ap.add_argument("--thresholds", default=None, help="JSON with ev_abs, psnr_gap_db, reward_rel")
    ap.add_argument("--no-raw-validation", action="store_true")
    args = ap.parse_args(argv)

    from camsim.report import write_report
    from camsim.validation import Thresholds, validate_png, validate_raw
    raw_dir = Path(args.rawframes) / args.sweep
    sim = build_sim(args)
    out = Path(args.outputs) / args.sweep / "report"
    result = validate_png(sim, out, K=args.K, seed=args.seed, thresholds=Thresholds.load(args.thresholds),
                          raw_dir=raw_dir if raw_dir.exists() else None, max_points=args.max_points, chunk=args.chunk)
    raw_res = None
    if not args.no_raw_validation and raw_dir.exists():
        raw_res = validate_raw(sim, raw_dir, seed=args.seed)
        if raw_res:
            from camsim.validation import write_csv
            write_csv(out / "raw_metrics.csv", [{k: v for k, v in r.items() if not k.startswith("curve")} for r in raw_res["rows"]])
    p = write_report(result, out, raw_res, title=f"camsim validation: {args.sweep}")
    s = result["summary"]
    print(f"{s['n_points']} points; EV error mean|.| {s['ev_error_full']['mean_abs']:.4f} max|.| {s['ev_error_full']['max_abs']:.4f}")
    for k, c in s["criteria"].items():
        print(f"  {k:22s} {'n/a' if c.get('pass') is None else ('PASS' if c['pass'] else 'FAIL')}")
    print("report:", p)


if __name__ == "__main__":
    main()
