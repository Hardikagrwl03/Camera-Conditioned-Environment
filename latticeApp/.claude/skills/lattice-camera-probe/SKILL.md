---
name: lattice-camera-probe
description: Inspect a device's Camera2 characteristics before designing a Lattice feature, and empirically probe hardware behaviour such as the focus actuator grid. Use when deciding whether a camera parameter can be controlled, or when a device behaves unexpectedly and the characteristics need checking.
---

# Probing the camera

Never assume a Camera2 key is controllable. Read the characteristic first — the white balance axis
exists only because the device turned out to report `MANUAL_POST_PROCESSING` and
`CONTROL_AWB_MODE_OFF`.

## Static characteristics

```bash
adb shell dumpsys media.camera > cam.txt
wc -l cam.txt          # expect thousands of lines

grep -inE "availableCapabilities|awbAvailableModes|colorCorrection.availableModes" cam.txt
grep -inE "referenceIlluminant|calibrationTransform|tonemap.availableToneMapModes" cam.txt
grep -inE "shading.availableModes|blackLevel|postRawSensitivityBoost" cam.txt
```

Read the line *after* each match for the values. What matters:

- `availableCapabilities` — `MANUAL_SENSOR` gates ISO/exposure/focus, `MANUAL_POST_PROCESSING` gates
  colour gains, `RAW` gates DNG capture.
- `awbAvailableModes` — needs **0 (OFF)** before gains can be commanded.
- `referenceIlluminant1/2` plus the calibration transforms — the data a colorimetric white balance
  would need.

The app also snapshots what it found into every `manifest.json` under `camera`, so an existing
session answers most of these without a device attached.

## Probing behaviour the characteristics do not report

Some things are only discoverable empirically. The focus actuator grid is the worked example: no
Camera2 key reports focus resolution, so it was measured by requesting closely spaced values and
reading `LENS_FOCUS_DISTANCE` back.

**Method.** Configure a sweep whose focus list clusters several values a fraction of a step apart at
each of a handful of base positions across the travel, run it, then compare requested against actual
in the manifest. Distinct actuals reveal the grid; repeated actuals reveal the quantum.

**Run it twice.** Determinism is a separate question from resolution, and it matters more. Both
probe runs here returned byte-identical actuals at all 36 positions, which is what makes focus
sweeps reproducible months apart.

**What that probe found**, for reference: the grid is not uniform. It fits
`step(D) ≈ 0.004114 − 0.00004326 × D`, shrinking about 11 % from infinity to closest focus, giving
roughly 2 568 distinct positions over 0–10 D. This is encoded in `focusGridStep()` in
`model/SweepDefaults.kt`, and is used **only** to cap counts and warn — never to alter generated
values, so a wrong law on a different device costs a suboptimal cap, not wrong data.

## Cleaning up after a probe

A probe is a real sweep and writes a real session. Follow the discipline in `lattice-verify`:
snapshot the session list first, then delete only by exact name.
