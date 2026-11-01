"""Windows-side scripts: dumpsys parser, adb output parsers, instrumentation output parser."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import adbutil
import capture
import collect_device_info as cdi

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "windows"

DUMPSYS = """\
== Service dump ==
Camera module API version: 0x3
Device 0 maps to "0"
Device 1 maps to "1"
== Camera HAL device device@1.1/internal/0 (v3.4) static information: ==
  Resource cost: 100
  Camera characteristics:
    android.colorCorrection.availableAberrationModes (f0000): byte[3]
      [OFF FAST HIGH_QUALITY ]
    android.control.postRawSensitivityBoostRange (10018): int32[2]
      [100 100 ]
    android.lens.info.shadingMapSize (f0007): int32[2]
      [17 13 ]
    android.sensor.info.activeArraySize (f0000): int32[4]
      [0 0 4080 3060 ]
    android.sensor.info.whiteLevel (f0000): int32[1]
      [1023 ]
    android.sensor.blackLevelPattern (f0000): int32[4]
      [0 0 0
       0 ]
    android.sensor.info.sensitivityRange (f0000): int32[2]
      [12 3200 ]
    android.sensor.info.physicalSize (f0000): float[2]
      [8.16 6.12 ]
    android.sensor.referenceIlluminant1 (f0000): byte[1]
      [D65 ]
    android.sensor.forwardMatrix1 (f0000): rational[9]
      [1024/1024 0/1024 0/1024 0/1024 1024/1024 0/1024 0/1024 0/1024 512/1024 ]
    android.tonemap.availableToneMapModes (f0000): byte[4]
      [CONTRAST_CURVE FAST HIGH_QUALITY GAMMA_VALUE ]
== Camera HAL device device@1.1/internal/1 (v3.4) static information: ==
    android.sensor.info.whiteLevel (f0000): int32[1]
      [255 ]
"""


def test_parse_dumpsys_selects_camera_and_values():
    p = cdi.parse_dumpsys(DUMPSYS, "0")
    assert p["android.sensor.info.whiteLevel"]["values"] == [1023]            # not camera 1's 255
    assert p["android.sensor.info.activeArraySize"]["values"] == [0, 0, 4080, 3060]
    assert p["android.sensor.blackLevelPattern"]["values"] == [0, 0, 0, 0]    # value split over two lines
    assert p["android.sensor.info.physicalSize"]["values"] == [8.16, 6.12]
    assert p["android.sensor.forwardMatrix1"]["values"][8] == 0.5
    assert p["android.colorCorrection.availableAberrationModes"]["values"] == ["OFF", "FAST", "HIGH_QUALITY"]
    assert cdi.parse_dumpsys(DUMPSYS, "1")["android.sensor.info.whiteLevel"]["values"] == [255]


def test_extract_profile_tolerates_missing_keys():
    prof, warns = cdi.extract_profile(cdi.parse_dumpsys(DUMPSYS, "0"))
    assert prof["white_level"] == 1023 and prof["black_level_pattern"] == [0, 0, 0, 0]
    assert prof["forward_matrix1"]["matrix"][2] == [0.0, 0.0, 0.5]
    assert prof["post_raw_sensitivity_boost_range"] == [100, 100]
    assert prof["noise_profile"] is None and any("noiseProfile" in w for w in warns)
    prof2, warns2 = cdi.extract_profile({})
    assert all(v is None for v in prof2.values()) and len(warns2) == len(cdi.WANTED)


def test_parse_dumpsys_crlf_and_garbage():
    p = cdi.parse_dumpsys(DUMPSYS.replace("\n", "\r\n"), "0")
    assert p["android.sensor.info.whiteLevel"]["values"] == [1023]
    assert cdi.parse_dumpsys("total garbage\n\x00\xff", "0") == {}


def test_adb_parsers():
    devs = adbutil.parse_devices("List of devices attached\nR58N123\tdevice usb:1-1 model:SM_S711B\nABC\tunauthorized\n\n")
    assert devs == [{"serial": "R58N123", "state": "device"}, {"serial": "ABC", "state": "unauthorized"}]
    props = adbutil.parse_getprop("[ro.product.model]: [SM-S711B]\r\n[ro.build.id]: [UP1A]\r\nnoise\r\n")
    assert props["ro.product.model"] == "SM-S711B" and props["ro.build.id"] == "UP1A"


OK_OUT = """\
INSTRUMENTATION_STATUS: numtests=1
INSTRUMENTATION_STATUS: rawcal=start mode=scene out=/sdcard/Android/data/dev.hamster.lattice/files/rawcal/s1
INSTRUMENTATION_STATUS_CODE: 0
INSTRUMENTATION_STATUS: rawcal=done files=12 out=/sdcard/Android/data/dev.hamster.lattice/files/rawcal/s1
INSTRUMENTATION_STATUS_CODE: 0
INSTRUMENTATION_STATUS: test=capture
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS: test=capture
INSTRUMENTATION_STATUS_CODE: 0
INSTRUMENTATION_RESULT: stream=

OK (1 test)
INSTRUMENTATION_CODE: -1
"""

FAIL_OUT = """\
INSTRUMENTATION_STATUS: test=capture
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS: stack=java.lang.AssertionError: RawCalibrationCaptureTest failed: no RAW image
	at org.junit.Assert.fail(Assert.java:89)
INSTRUMENTATION_STATUS: test=capture
INSTRUMENTATION_STATUS_CODE: -2
INSTRUMENTATION_CODE: -1
"""


def test_parse_instrument_output():
    r = capture.parse_instrument_output(OK_OUT)
    assert r.ok and r.failure is None and r.out_dir.endswith("rawcal/s1") and len(r.status) == 2
    f = capture.parse_instrument_output(FAIL_OUT)
    assert not f.ok and "no RAW image" in f.failure
    crash = capture.parse_instrument_output("INSTRUMENTATION_RESULT: shortMsg=Process crashed.\nINSTRUMENTATION_CODE: 0\n")
    assert not crash.ok and "crashed" in crash.failure
    assert not capture.parse_instrument_output("Error: Unable to find instrumentation info").ok


def test_instrument_cmd_and_scene_validation(tmp_path):
    cmd = capture.build_instrument_cmd({"mode": "scene", "focusD": "1", "isos": "", "outName": "x"})
    assert cmd[:3] == ["am", "instrument", "-w"] and "isos" not in cmd and cmd[-1].endswith("AndroidJUnitRunner")
    from helpers import make_sweep_dir
    make_sweep_dir(tmp_path, [100], [1_000_000], [0.0, 1.0, 3.98107], ds=3, name="s1")
    info = capture.validate_scene_args(tmp_path, "s1", 1.0)
    assert info["downscale"] == 3
    with pytest.raises(SystemExit):
        capture.validate_scene_args(tmp_path, "s1", 2.0)           # not a sweep focus value
    with pytest.raises(SystemExit):
        capture.validate_scene_args(tmp_path, "missing", 1.0)


def test_verify_pulled(tmp_path):
    assert capture.verify_pulled(tmp_path)
    (tmp_path / "capture_manifest.json").write_text(json.dumps({"entries": [{"file": "a.dng", "png": "a.png", "json": "a.json"}]}))
    (tmp_path / "characteristics.json").write_text("{}")
    (tmp_path / "a.dng").write_bytes(b"x")
    probs = capture.verify_pulled(tmp_path)
    assert any("a.png" in p for p in probs) and any("a.json" in p for p in probs)
    (tmp_path / "a.png").write_bytes(b"x"); (tmp_path / "a.json").write_text("{}")
    assert capture.verify_pulled(tmp_path) == []


@pytest.mark.parametrize("script", ["collect_device_info.py", "capture.py"])
def test_help_runs(script):
    r = subprocess.run([sys.executable, str(SCRIPTS / script), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "usage" in r.stdout.lower()
