#!/usr/bin/env python
"""Fit sensor + ISP + alignment -> calibration/<device>/ from the pulled raw captures.

Expected input: calibration/<device>/raw_captures/{dark,flat,pairs/<scene>...,yuvonly}/ (from
scripts/windows/capture.py), and optionally rawframes/<sweep>/ whose scene pair (tag raw, with PNG)
joins the ISP fit. Every part is optional; missing parts fall back as documented in the README.
"""
import argparse
import json
from pathlib import Path

from _common import REPO


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--calibration", default=str(REPO / "calibration"))
    ap.add_argument("--rawframes", default=str(REPO / "rawframes"))
    ap.add_argument("--device-name", required=True, help="folder name under calibration/, e.g. SM-S711B")
    ap.add_argument("--include-scene-pairs", action="store_true", help="also use rawframes/*/ scene pairs for the ISP fit")
    ap.add_argument("--torch-device", default="cpu")
    ap.add_argument("--residual", action="store_true", help="train the optional learned residual (isp/residual.py)")
    args = ap.parse_args(argv)

    from camsim.calibration import Calibration
    from camsim.fitting import collect_pairs, fit_isp, fit_sensor
    from camsim.isp.pipeline import default_isp_params
    root = Path(args.calibration) / args.device_name
    raw_root = root / "raw_captures"
    cal = Calibration(root=root)
    print("== sensor ==")
    sensor, dsnu, shading, rep = fit_sensor(raw_root)
    cal.sensor, cal.dsnu_e_s = sensor, dsnu
    cal.report["sensor"] = rep
    for n in rep.get("notes", []):
        print("  note:", n)

    print("== ISP ==")
    dirs = sorted([d for d in (raw_root / "pairs").glob("*") if d.is_dir()]) if (raw_root / "pairs").exists() else []
    if args.include_scene_pairs:
        dirs += sorted(d for d in Path(args.rawframes).glob("*") if d.is_dir())
    pairs = collect_pairs(dirs)
    if pairs:
        base = default_isp_params(pairs[0].frame)
        if shading is not None:
            base.shading = shading
        params, align, irep = fit_isp(pairs, base, device=args.torch_device)
        cal.isp, cal.alignment = params, align
        cal.report["isp"] = irep
        if irep.get("tone_varies"):
            print("  WARNING:", irep.get("recommendation"))
    else:
        print("  no (DNG, PNG) pairs found; ISP not fitted (the simulator will fit a tone curve from the scene pair)")
    cal.save(root)
    print("saved calibration to", root)
    if args.residual:
        print("The learned residual needs >=2 scenes; see README 'Learned residual'.")


if __name__ == "__main__":
    main()
