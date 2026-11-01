"""Shared bits for the Linux scripts."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def add_common_args(ap):
    ap.add_argument("--sweep", required=True, help="sweep session name, e.g. 20260911_144103")
    ap.add_argument("--framesweeps", default=str(REPO / "framesweeps"))
    ap.add_argument("--rawframes", default=str(REPO / "rawframes"))
    ap.add_argument("--calibration", default=str(REPO / "calibration"))
    ap.add_argument("--outputs", default=str(REPO / "outputs"))
    ap.add_argument("--device", default=None, help="torch device (default: cuda if available, else cpu)")
    ap.add_argument("--focus", type=float, default=None, help="focus of the raw frame (default: from sidecar/filename)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=None, help="batch elements rendered at once (default: auto from free memory)")


def build_sim(args):
    from camsim.simulator import CameraSimulator
    return CameraSimulator.from_dirs(Path(args.framesweeps) / args.sweep, Path(args.rawframes) / args.sweep,
                                     args.calibration, device=args.device, focus_d=args.focus)
