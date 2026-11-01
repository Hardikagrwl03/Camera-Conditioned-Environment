#!/usr/bin/env python
"""Collect camera characteristics from a connected Android phone.

Writes device/<model>/device_profile.json plus the raw `dumpsys media.camera` text. Python 3.10+,
standard library only; works in PowerShell / cmd / bash.

    python scripts/windows/collect_device_info.py [--serial XYZ] [--camera-id 0] [--adb PATH] [--out device]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adbutil  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

# friendly name -> dumpsys key
WANTED = {
    "color_filter_arrangement": "android.sensor.info.colorFilterArrangement",
    "black_level_pattern": "android.sensor.blackLevelPattern",
    "white_level": "android.sensor.info.whiteLevel",
    "pixel_array_size": "android.sensor.info.pixelArraySize",
    "active_array_size": "android.sensor.info.activeArraySize",
    "physical_size_mm": "android.sensor.info.physicalSize",
    "sensitivity_range": "android.sensor.info.sensitivityRange",
    "max_analog_sensitivity": "android.sensor.maxAnalogSensitivity",
    "exposure_time_range_ns": "android.sensor.info.exposureTimeRange",
    "max_frame_duration_ns": "android.sensor.info.maxFrameDuration",
    "post_raw_sensitivity_boost_range": "android.control.postRawSensitivityBoostRange",
    "color_transform1": "android.sensor.colorTransform1",
    "color_transform2": "android.sensor.colorTransform2",
    "forward_matrix1": "android.sensor.forwardMatrix1",
    "forward_matrix2": "android.sensor.forwardMatrix2",
    "calibration_transform1": "android.sensor.calibrationTransform1",
    "calibration_transform2": "android.sensor.calibrationTransform2",
    "reference_illuminant1": "android.sensor.referenceIlluminant1",
    "reference_illuminant2": "android.sensor.referenceIlluminant2",
    "noise_profile": "android.sensor.noiseProfile",
    "tonemap_available_modes": "android.tonemap.availableToneMapModes",
    "tonemap_max_curve_points": "android.tonemap.maxCurvePoints",
    "shading_available_modes": "android.shading.availableModes",
    "hot_pixel_available_modes": "android.hotPixel.availableHotPixelModes",
    "distortion_correction_available_modes": "android.distortionCorrection.availableModes",
    "aberration_available_modes": "android.colorCorrection.availableAberrationModes",
    "noise_reduction_available_modes": "android.noiseReduction.availableNoiseReductionModes",
    "edge_available_modes": "android.edge.availableEdgeModes",
    "lens_shading_map_size": "android.lens.info.shadingMapSize",
    "available_capabilities": "android.request.availableCapabilities",
    "lens_focal_lengths": "android.lens.info.availableFocalLengths",
    "lens_apertures": "android.lens.info.availableApertures",
    "min_focus_distance": "android.lens.info.minimumFocusDistance",
    "hyperfocal_distance": "android.lens.info.hyperfocalDistance",
    "focus_distance_calibration": "android.lens.info.focusDistanceCalibration",
    "sensor_orientation": "android.sensor.orientation",
    "timestamp_source": "android.sensor.info.timestampSource",
}
MATRIX_KEYS = {"color_transform1", "color_transform2", "forward_matrix1", "forward_matrix2",
               "calibration_transform1", "calibration_transform2"}

_KEY_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)\s*\(([0-9a-fA-F]+)\)\s*:\s*(\w+)\[(\d+)\]\s*$")
_RATIONAL = re.compile(r"^(-?\d+)/(-?\d+)$")


def parse_token(tok: str) -> Any:
    tok = tok.strip()
    m = _RATIONAL.match(tok)
    if m:
        d = int(m.group(2))
        return int(m.group(1)) / d if d else None
    for conv in (int, float):
        try:
            return conv(tok)
        except ValueError:
            pass
    return tok


def split_camera_blocks(text: str) -> List[Tuple[str, List[str]]]:
    """Split a dumpsys dump into (header, lines) blocks, one per camera device section."""
    blocks: List[Tuple[str, List[str]]] = []
    cur_h, cur = "", []
    hdr = re.compile(r"^\s*(==\s*Camera HAL device .*|Camera ID \d+.*|Camera device \d+.*|\s*Device \d+ maps to.*)$")
    for line in text.split("\n"):
        if hdr.match(line) and ("static" in line.lower() or "HAL device" in line or "Camera ID" in line or "Camera device" in line):
            blocks.append((cur_h, cur))
            cur_h, cur = line.strip(), []
        else:
            cur.append(line)
    blocks.append((cur_h, cur))
    return blocks


def select_block(text: str, camera_id: str) -> Tuple[str, List[str]]:
    """The section for ``camera_id``; falls back to the whole dump with a note in the header."""
    for h, lines in split_camera_blocks(text):
        if not h:
            continue
        if re.search(rf"/{re.escape(camera_id)}\b", h) or re.search(rf"(?:ID|device)\s+{re.escape(camera_id)}\b", h):
            if any(_KEY_LINE.match(l) for l in lines):
                return h, lines
    return "(whole dump)", text.split("\n")


def parse_dumpsys(text: str, camera_id: str = "0") -> Dict[str, Dict[str, Any]]:
    """Parse `dumpsys media.camera` into {key: {"type": str, "count": int, "values": [...]}}.

    Only the first occurrence of each key inside the selected camera's section is kept (static
    characteristics come before the default request templates). Tolerant: unknown lines are
    ignored, values spanning several bracket lines are concatenated.
    """
    text = adbutil.norm(text)
    _, lines = select_block(text, camera_id)
    out: Dict[str, Dict[str, Any]] = {}
    i = 0
    while i < len(lines):
        m = _KEY_LINE.match(lines[i])
        if not m:
            i += 1
            continue
        key, _, typ, count = m.group(1), m.group(2), m.group(3), int(m.group(4))
        vals: List[Any] = []
        j = i + 1
        while j < len(lines) and lines[j].lstrip().startswith("["):
            # one bracket block, possibly wrapped over several lines until the closing ']'
            chunk = lines[j].strip()
            while "]" not in chunk and j + 1 < len(lines) and not _KEY_LINE.match(lines[j + 1]):
                j += 1
                chunk += " " + lines[j].strip()
            inner = chunk.lstrip("[").split("]")[0]
            vals.extend(parse_token(t) for t in inner.split())
            j += 1
        if key not in out:
            out[key] = {"type": typ, "count": count, "values": vals}
        i = max(j, i + 1)
    return out


def extract_profile(parsed: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """Map the characteristics we need to friendly names; missing keys -> null + warning."""
    prof: Dict[str, Any] = {}
    warns: List[str] = []
    for name, key in WANTED.items():
        e = parsed.get(key)
        if e is None:
            prof[name] = None
            warns.append(f"{key} not found in dumpsys output")
            continue
        vals = e["values"]
        if name in MATRIX_KEYS and len(vals) == 9 and all(isinstance(v, (int, float)) for v in vals):
            prof[name] = {"flat": vals, "matrix": [vals[0:3], vals[3:6], vals[6:9]]}
        elif len(vals) == 1:
            prof[name] = vals[0]
        else:
            prof[name] = vals
        if e["count"] and len(vals) != e["count"] and not (len(vals) == 1 and isinstance(vals[0], str)):
            warns.append(f"{key}: header says {e['count']} values but parsed {len(vals)}")
    return prof, warns


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_") or "device"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Collect Android camera characteristics via adb.")
    ap.add_argument("--adb", help="path to adb (default: ANDROID_HOME/platform-tools or PATH)")
    ap.add_argument("--serial", help="device serial (required if several devices are attached)")
    ap.add_argument("--camera-id", default="0", help="camera id to profile (default 0)")
    ap.add_argument("--out", default=str(REPO / "device"), help="output root (default <repo>/device)")
    args = ap.parse_args(argv)

    try:
        adb = adbutil.select_device(adbutil.find_adb(args.adb), args.serial)
        props = adbutil.parse_getprop(adb.shell("getprop", timeout=60))
        model = props.get("ro.product.model", "unknown")
        dump = adb.shell("dumpsys", "media.camera", timeout=180)
    except adbutil.AdbError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    out_dir = Path(args.out) / safe_name(model)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dumpsys_media_camera.txt").write_text(dump, encoding="utf-8", newline="\n")
    parsed = parse_dumpsys(dump, args.camera_id)
    prof, warns = extract_profile(parsed)
    if not parsed:
        warns.append("no characteristics parsed: the dumpsys format may differ; see dumpsys_media_camera.txt")
    profile = {
        "model": model, "manufacturer": props.get("ro.product.manufacturer"),
        "device": props.get("ro.product.device"), "build_id": props.get("ro.build.id"),
        "build_fingerprint": props.get("ro.build.fingerprint"),
        "android_release": props.get("ro.build.version.release"), "sdk": props.get("ro.build.version.sdk"),
        "camera_id": args.camera_id, "characteristics": prof, "warnings": warns,
        "raw_dump": "dumpsys_media_camera.txt", "n_keys_parsed": len(parsed),
    }
    (out_dir / "device_profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    print(f"device: {model} (camera {args.camera_id}); {len(parsed)} keys parsed")
    for k in ("white_level", "black_level_pattern", "sensitivity_range", "exposure_time_range_ns"):
        print(f"  {k}: {prof.get(k)}")
    for w in warns:
        print(f"  warning: {w}")
    print(f"wrote {out_dir / 'device_profile.json'}")
    print("Next: capture.py dark / flat / pairs / yuvonly, then copy the whole folder to the Linux server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
