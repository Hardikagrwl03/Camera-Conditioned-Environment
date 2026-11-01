#!/usr/bin/env python
"""Drive the Lattice RawCalibrationCaptureTest from a PC and pull the results.

    python scripts/windows/capture.py scene   --sweep 20260911_144103 --focus 1.0
    python scripts/windows/capture.py dark                      (lens covered)
    python scripts/windows/capture.py flat                      (uniform diffuse target)
    python scripts/windows/capture.py pairs   --name kitchen    (a varied scene; >= 3 scenes)
    python scripts/windows/capture.py yuvonly

Python 3.10+, standard library only. Builds and installs the app + test APKs through the app
repo's gradle wrapper, grants CAMERA, runs `am instrument`, streams and checks its output, pulls
the output folder and verifies the file list against capture_manifest.json.

Never touches anything on the phone except /sdcard/Android/data/dev.hamster.lattice/files/rawcal/
(and only deletes with --cleanup, by exact folder name).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adbutil  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
APP_PKG = "dev.hamster.lattice"
TEST_PKG = "dev.hamster.lattice.test"
RUNNER = "androidx.test.runner.AndroidJUnitRunner"
TEST_CLASS = "dev.hamster.lattice.RawCalibrationCaptureTest"
REMOTE_ROOT = f"/sdcard/Android/data/{APP_PKG}/files/rawcal"
APK_APP = Path("app/build/outputs/apk/debug/app-debug.apk")
APK_TEST = Path("app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk")


# ----------------------------------------------------------------------------------------------
# pure helpers (unit-tested)
# ----------------------------------------------------------------------------------------------
@dataclass
class InstrumentResult:
    ok: bool
    status: List[str] = field(default_factory=list)       # rawcal=... progress lines
    failure: Optional[str] = None
    out_dir: Optional[str] = None


def parse_instrument_output(text: str) -> InstrumentResult:
    """Interpret `am instrument -w -r` output.

    Success needs a per-test status code 0 and no failure code (-1 error, -2 failure) and no
    crash / shortMsg in INSTRUMENTATION_RESULT.
    """
    text = adbutil.norm(text)
    status = [m.group(1).strip() for m in re.finditer(r"^INSTRUMENTATION_STATUS: rawcal=(.*)$", text, re.M)]
    codes = [int(m.group(1)) for m in re.finditer(r"^INSTRUMENTATION_STATUS_CODE: (-?\d+)$", text, re.M)]
    failure = None
    if any(c in (-1, -2) for c in codes):
        m = re.search(r"^INSTRUMENTATION_STATUS: stack=(.*?)(?=^INSTRUMENTATION_STATUS)", text, re.M | re.S)
        failure = (m.group(1).strip() if m else "test reported failure")[:2000]
    m = re.search(r"^INSTRUMENTATION_RESULT: shortMsg=(.*)$", text, re.M)
    if m:
        failure = failure or f"instrumentation aborted: {m.group(1).strip()}"
    m = re.search(r"^INSTRUMENTATION_RESULT: longMsg=(.*)$", text, re.M)
    if m and failure:
        failure += f" ({m.group(1).strip()})"
    if re.search(r"^Time: .*\n\s*\n?FAILURES!!!", text, re.M):
        failure = failure or "FAILURES!!!"
    ok = failure is None and 0 in codes
    if failure is None and 0 not in codes:
        failure = "no test result received (instrumentation did not run the test: check the APK install and -e class)"
    out_dir = None
    for s in reversed(status):
        m2 = re.search(r"out=(\S+)", s) or re.search(r"out=(\S+)", s)
        if m2:
            out_dir = m2.group(1)
            break
    return InstrumentResult(ok, status, failure, out_dir)


def build_instrument_cmd(args: Dict[str, str]) -> List[str]:
    cmd = ["am", "instrument", "-w", "-r", "-e", "class", TEST_CLASS]
    for k, v in args.items():
        if v is not None and v != "":
            cmd += ["-e", k, str(v)]
    cmd.append(f"{TEST_PKG}/{RUNNER}")
    return cmd


def load_sweep_focus_values(sweep_dir: Path) -> List[float]:
    m = json.loads((sweep_dir / "manifest.json").read_text(encoding="utf-8"))
    return [float(v) for v in m["config"]["focusValuesDiopters"]]


def validate_scene_args(framesweeps: Path, sweep: str, focus: float, tol: float = 0.011) -> Dict[str, object]:
    """The sweep session must exist in framesweeps/ and the focus must be one of its focus values."""
    d = framesweeps / sweep
    if not d.is_dir():
        raise SystemExit(f"error: sweep session '{sweep}' not found in {framesweeps} "
                         "(pull the sweep from the phone into framesweeps/ first, or fix --sweep)")
    if not (d / "manifest.json").exists():
        raise SystemExit(f"error: {d / 'manifest.json'} is missing")
    vals = load_sweep_focus_values(d)
    if not any(abs(v - focus) <= tol for v in vals):
        raise SystemExit("error: --focus %g is not one of the sweep's focus values (diopters):\n  %s"
                         % (focus, ", ".join(f"{v:.4g}" for v in vals)))
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    nearest = min(vals, key=lambda v: abs(v - focus))
    return {"downscale": int(m["config"].get("downscale", 2)), "focus": nearest}


def expected_files(manifest: dict) -> List[str]:
    files = ["capture_manifest.json", "characteristics.json"]
    for e in manifest.get("entries", []):
        for k in ("file", "png", "pngFull", "json"):
            if e.get(k):
                files.append(e[k])
    return sorted(set(files))


def verify_pulled(local: Path) -> List[str]:
    """Return the list of problems (empty = ok) comparing the pulled folder with capture_manifest.json."""
    mp = local / "capture_manifest.json"
    if not mp.exists():
        return ["capture_manifest.json missing (the test did not finish writing)"]
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    problems = [f"missing file: {f}" for f in expected_files(manifest) if not (local / f).exists()]
    problems += [f"empty file: {f}" for f in expected_files(manifest) if (local / f).exists() and (local / f).stat().st_size == 0]
    return problems


# ----------------------------------------------------------------------------------------------
def stream(cmd: List[str], cwd: Optional[str] = None, quiet_prefix: bool = False) -> str:
    """Run a command, echo its output line by line, return everything (line endings normalised)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd)
    lines = []
    assert p.stdout is not None
    for raw in iter(p.stdout.readline, b""):
        line = adbutil.norm(raw.decode("utf-8", errors="replace")).rstrip("\n")
        lines.append(line)
        if line.startswith("INSTRUMENTATION_STATUS: rawcal="):
            print("  [phone]", line.split("rawcal=", 1)[1])
        elif not line.startswith("INSTRUMENTATION_") and line.strip():
            print("  " + line)
    p.wait()
    out = "\n".join(lines)
    if p.returncode not in (0, None):
        out += f"\n[exit code {p.returncode}]"
    return out


def build_and_install(adb: adbutil.Adb, app_dir: Path, skip_build: bool) -> None:
    if not skip_build:
        gradle = app_dir / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if not gradle.exists():
            raise SystemExit(f"error: {gradle} not found; pass --app-dir <latticeApp clone> (branch 'application')")
        print("== building app + test APKs (gradle) ==")
        out = stream([str(gradle), ":app:assembleDebug", ":app:assembleDebugAndroidTest"], cwd=str(app_dir))
        if "BUILD SUCCESSFUL" not in out:
            raise SystemExit("error: gradle build failed (see output above). Does the 'application' branch contain "
                             "RawCalibrationCaptureTest.kt?")
    for apk in (APK_APP, APK_TEST):
        p = app_dir / apk
        if not p.exists():
            raise SystemExit(f"error: {p} not found; run without --skip-build")
    print("== installing ==")
    for apk in (APK_APP, APK_TEST):
        out = adb("install", "-r", "-t", "-g", str(app_dir / apk), timeout=300)
        if "Success" not in out:
            raise SystemExit(f"error: adb install {apk} failed:\n{out}")
    adb.shell("pm", "grant", APP_PKG, "android.permission.CAMERA", check=False)
    adb.shell("pm", "grant", TEST_PKG, "android.permission.CAMERA", check=False)   # harmless if not declared
    adb.shell("input", "keyevent", "KEYCODE_WAKEUP", check=False)


def remote_exists(adb: adbutil.Adb, path: str) -> bool:
    out = adb.shell("ls", path, check=False)
    return "No such file" not in out and out.strip() != ""


def pull(adb: adbutil.Adb, remote: str, local: Path) -> None:
    local.mkdir(parents=True, exist_ok=True)
    adb("pull", remote + "/.", str(local), timeout=3600)


def destination(mode: str, args, device_name: str) -> Path:
    if mode == "scene":
        return Path(args.rawframes) / args.sweep
    base = Path(args.calibration) / device_name / "raw_captures"
    if mode == "pairs":
        return base / "pairs" / args.name
    return base / mode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--adb", help="path to adb")
    common.add_argument("--serial", help="device serial if several are attached")
    common.add_argument("--app-dir", default=str((REPO / ".." / ".." / "application" / "latticeApp").resolve()),
                        help="latticeApp clone, branch 'application' (default ../../application/latticeApp)")
    common.add_argument("--skip-build", action="store_true", help="reuse the APKs already built")
    common.add_argument("--cleanup", action="store_true", help="delete the capture folder on the phone after a verified pull")
    common.add_argument("--framesweeps", default=str(REPO / "framesweeps"))
    common.add_argument("--rawframes", default=str(REPO / "rawframes"))
    common.add_argument("--calibration", default=str(REPO / "calibration"))
    common.add_argument("--focus", type=float, default=1.0, help="focus in diopters (scene: must be a sweep focus value)")
    common.add_argument("--downscale", type=int, default=None, help="PNG downscale (scene: from the sweep manifest)")
    common.add_argument("--isos", help="comma list, overrides the mode default")
    common.add_argument("--exposures-ns", help="comma list in ns, overrides the mode default")
    common.add_argument("--frames", type=int, help="frames per setting, overrides the mode default")
    common.add_argument("--arg", action="append", default=[], metavar="KEY=VALUE",
                        help="extra instrumentation argument (see the test's KDoc), repeatable")
    common.add_argument("--yes", action="store_true", help="do not wait for the set-up confirmation")
    common.add_argument("--force", action="store_true", help="pull into a folder that already holds a capture_manifest.json")

    p = sub.add_parser("scene", parents=[common], help="burst + bracket + validation grid right after a sweep")
    p.add_argument("--sweep", required=True, help="sweep session name, e.g. 20260911_144103")
    sub.add_parser("dark", parents=[common], help="dark frames (lens covered)")
    sub.add_parser("flat", parents=[common], help="flat-field ramps (uniform diffuse target)")
    p = sub.add_parser("pairs", parents=[common], help="DNG+PNG pairs of another scene for ISP fitting")
    p.add_argument("--name", required=True, help="scene name")
    sub.add_parser("yuvonly", parents=[common], help="YUV-only session to compare with the paired captures")
    args = ap.parse_args(argv)

    instr: Dict[str, str] = {"mode": args.mode, "focusD": f"{args.focus:g}"}
    if args.mode == "scene":
        info = validate_scene_args(Path(args.framesweeps), args.sweep, args.focus)
        instr["session"] = args.sweep
        instr["downscale"] = str(args.downscale or info["downscale"])
        label = args.sweep
    else:
        instr["downscale"] = str(args.downscale or 2)
        label = getattr(args, "name", None) or args.mode
    if args.isos:
        instr["isos"] = args.isos
    if args.exposures_ns:
        instr["exposuresNs"] = args.exposures_ns
    if args.frames:
        instr["frames"] = str(args.frames)
    for kv in args.arg:
        k, _, v = kv.partition("=")
        instr[k] = v

    try:
        adb = adbutil.select_device(adbutil.find_adb(args.adb), args.serial)
        model = adbutil.parse_getprop(adb.shell("getprop", timeout=60)).get("ro.product.model", "unknown")
    except adbutil.AdbError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    device_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", model).strip("_")
    dest = destination(args.mode, args, device_name)
    if (dest / "capture_manifest.json").exists() and not args.force:
        print(f"ERROR: {dest} already contains a capture (capture_manifest.json). Use --force to pull into it anyway, "
              "or move the old folder away.", file=sys.stderr)
        return 2

    reminders = {"dark": "COVER THE LENS completely (cap + dark cloth).",
                 "flat": "Point the phone at a UNIFORM, diffusely lit target (e.g. white paper, defocus not needed).",
                 "scene": "Do NOT touch the rig since the sweep (tripod, lighting, scene).",
                 "pairs": "Frame a varied, static scene; this is not the evaluation scene.",
                 "yuvonly": "Use the same scene and rig as the sweep."}
    print(f"device {model} ({adb.serial}); mode {args.mode}; output -> {dest}")
    print("REMINDER:", reminders[args.mode])
    if not args.yes and sys.stdin and sys.stdin.isatty():
        input("Press Enter when ready (Ctrl+C aborts)... ")

    try:
        build_and_install(adb, Path(args.app_dir), args.skip_build)
        out_name = f"{label}_{time.strftime('%Y%m%d_%H%M%S')}"
        instr["outName"] = out_name
        remote = f"{REMOTE_ROOT}/{out_name}"
        print(f"== running {args.mode} (this can take several minutes; keep the phone awake) ==")
        out = stream([*adb.base(), "shell", *build_instrument_cmd(instr)])
        res = parse_instrument_output(out)
        if not res.ok:
            print(f"\nERROR: capture test failed: {res.failure}", file=sys.stderr)
            print(f"Partial output (if any) stays on the phone at {remote}", file=sys.stderr)
            return 1
        print("== pulling ==")
        pull(adb, remote, dest)
        problems = verify_pulled(dest)
        if problems:
            print("ERROR: pulled folder does not match capture_manifest.json:", file=sys.stderr)
            for pr in problems[:20]:
                print("  ", pr, file=sys.stderr)
            return 1
        nfiles = len(expected_files(json.loads((dest / "capture_manifest.json").read_text(encoding="utf-8"))))
        size_mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
        if args.cleanup:
            if re.fullmatch(re.escape(REMOTE_ROOT) + r"/[A-Za-z0-9_.-]+", remote):
                adb.shell("rm", "-rf", remote)
                print("deleted", remote, "from the phone")
        print("\n== checklist ==")
        print(f"  [x] {nfiles} files verified against capture_manifest.json ({size_mb:.0f} MB) in {dest}")
        print(f"  [{'x' if args.cleanup else ' '}] phone copy removed" + ("" if args.cleanup else f"  (still at {remote}; use --cleanup)"))
        nxt = {"scene": "pull the sweep into framesweeps/<session>/ if not done, then copy the whole cameraEnvironment folder to the server",
               "dark": "next: capture.py flat", "flat": "next: capture.py pairs --name <scene> (>= 3 scenes)",
               "pairs": "repeat for >= 3 varied scenes, then capture.py yuvonly", "yuvonly": "copy the whole folder to the server"}[args.mode]
        print("  next:", nxt)
        return 0
    except adbutil.AdbError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
