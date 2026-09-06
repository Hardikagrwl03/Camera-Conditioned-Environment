---
name: lattice-session-check
description: Pull a capture session off the device and validate its manifest - requested versus actual values, settle flags, distinct positions and coverage. Use after running a sweep, when checking whether captured data is trustworthy, or when investigating whether a setting actually reached the sensor.
---

# Checking a capture session

## Pull it

```bash
adb shell ls /sdcard/Lattice/ | tr -d '\r'
adb pull /sdcard/Lattice/<session>/manifest.json .
adb pull /sdcard/Lattice/<session>/metadata.csv .
```

**A session may be a directory or a `.zip`.** The Zip button on the finished screen archives a
session and deletes the folder, so `ls` can return either. For an archived one, pull and unpack it
instead — the entries are flat, with no directory prefix:

```bash
adb pull /sdcard/Lattice/<session>.zip .
unzip -o <session>.zip -d <session>/
```

The frames inside are stored uncompressed, so unpacking is fast and the archive is the same size as
the folder was.

A session cancelled part-way still has a valid manifest covering what was captured. A partial
session is a usable dataset, not a corrupt one.

## The checks that matter

**Requested is not actual.** Always analyse the `actual*` fields. Requesting 1.000 D lands at
0.999039 D on this rig; exposure snaps to a line-time step; colour gains may be clamped by the HAL.

```python
import json
m = json.load(open("manifest.json"))
caps = m["captures"]

# 1. did every frame settle?
bad = [c["index"] for c in caps if not c["settled"]]
print(f"unsettled: {len(bad)}/{len(caps)}", bad[:10])

# 2. did the axis actually move? distinct actual positions
act = [c["actualFocusDiopters"] for c in caps]
print(f"distinct focus positions: {len(set(act))}/{len(act)}")

# 3. how far did each request miss?
for c in sorted(caps, key=lambda c: c["requestedFocusDiopters"])[:10]:
    r, a = c["requestedFocusDiopters"], c["actualFocusDiopters"]
    print(f"{r:10.4f} -> {a:10.6f}  err {a-r:+.5f}")
```

**Distinct count below frame count means duplicates.** Two requests landed on the same physical
position, so those frames are the same capture twice. For focus this means the step was below the
actuator grid: about 0.0041 D near infinity, shrinking to 0.0037 D at closest focus.

**Colour gains prove white balance reached the sensor.** `actualColorGains` is `[r, gEven, gOdd, b]`.
Across a temperature sweep the red-to-blue ratio must rise monotonically with kelvin. Note the gains
are normalised so the smallest is 1.0, which pins the red gain flat across the warm half — the R/B
ratio is the invariant, not the individual channels.

**`awbState`** reads locked on the AUTO path and inactive on a manual sweep. Inactive on a sweep you
expected to be AUTO means the manual path ran.

## Coverage, for focus sweeps

Defocus blur is linear in diopters at about 8.53 px/D on this rig, so a gap of `d` diopters between
neighbouring focus values leaves a worst-case blur of `d/2 × 8.53` pixels for a subject falling
between them. A gap-free survey at 1 px needs steps of 0.117 D — 86 values over the full range.

```python
act = sorted(c["actualFocusDiopters"] for c in caps)
gaps = [b - a for a, b in zip(act, act[1:])]
print(f"largest gap {max(gaps):.3f} D = {max(gaps)*8.53/2:.1f} px worst-case blur")
```

## Sanity checks on the images themselves

Frames are processed 8-bit output — tonemapped and gamma-encoded — so pixel values are **not**
linear in scene radiance. Use them for relative comparisons, not absolute photometry.

```python
from PIL import Image
im = Image.open(f).convert("RGB").resize((80, 60))
px = list(im.getdata()); n = len(px)
r, g, b = (sum(p[i] for p in px)/n for i in range(3))
```
