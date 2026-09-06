# Experiments and Sampling Rationale

Why each axis in Lattice is sampled the way it is, what was measured on the hardware to decide it,
and which approaches were tried and rejected.

Every number here was measured or derived on the rig described in §1. Where a figure came from a
probe, the method is given so it can be repeated on other hardware — none of it should be trusted
blindly on a different device.

---

## Contents

1. [The rig](#1-the-rig)
2. [The governing principle](#2-the-governing-principle)
3. [ISO and shutter](#3-iso-and-shutter)
4. [Focus — the long one](#4-focus--the-long-one)
5. [White balance](#5-white-balance)
6. [Settling](#6-settling)
7. [Averaging, downscaling and format](#7-averaging-downscaling-and-format)
8. [What is still uncontrolled](#8-what-is-still-uncontrolled)

---

## 1. The rig

Samsung SM-S711B, rear camera 0, read from `CameraCharacteristics`:

| Property | Value |
|---|---|
| Focal length `f` | 5.540 mm |
| Aperture `N` | f/1.80 (fixed — no iris) |
| Sensor physical size | 8.160 × 6.120 mm |
| Pixel array | 4080 × 3060 |
| **Pixel pitch** | **2.000 µm** (exactly, both axes) |
| ISO range | 50 – 3200 |
| Exposure range | 85 µs – 100 ms |
| Minimum focus distance | 10.00 D (10 cm) |
| Hyperfocal distance | 0.11722 D |
| Focus distance calibration | `CALIBRATED` |
| Raw white level | 1023 (10-bit) |
| Black level pattern | 0, 0, 0, 0 |
| Hardware level | `FULL` |
| Capabilities | `MANUAL_SENSOR`, `MANUAL_POST_PROCESSING`, `RAW`, `BURST_CAPTURE` |

`focusDistanceCalibration = CALIBRATED` is what makes any of the focus work meaningful: it means
`LENS_FOCUS_DISTANCE` is in real diopters with a known zero, not an arbitrary vendor scale.

### Derived optical constants

Defocus blur diameter for a defocus of `ΔD` diopters:

```
c = (f² / N) · ΔD
```

```
k = f²/N = (5.540 mm)² / 1.80 = 17.051 µm per diopter
        k / pitch = 17.051 / 2.000 = 8.525 pixels per diopter
```

So **one pixel of defocus blur corresponds to 0.1173 D**, everywhere on the travel — the relation
is linear in diopters with no distance dependence.

Diffraction floor, at λ = 550 nm:

```
Airy diameter = 2.44 · λ · N = 2.44 × 0.55 µm × 1.80 = 2.416 µm = 1.208 pixels
```

### A confirmation worth having

The device reports its own hyperfocal distance as 0.11722 D. Hyperfocal *is* the defocus that
produces a blur equal to whatever circle of confusion the vendor assumed, so that figure can be
inverted:

```
c = H · k = 0.11722 D × 17.051 µm/D = 1.9987 µm = 0.9993 pixels
```

**The vendor's own hyperfocal is defined with a one-pixel circle of confusion.** That is an
independent confirmation of the `k` derived above — two unrelated routes agreeing to 0.07 % — and it
justifies adopting one pixel as the sampling criterion throughout, since it is the same criterion
the hardware's own metadata uses.

---

## 2. The governing principle

**Sample uniformly in the measure where equal steps are equal physical change.**

The natural-seeming unit is almost never the right one:

| Axis | Natural unit | Correct unit | Why |
|---|---|---|---|
| ISO | linear gain | log₂ (stops) | Response is multiplicative |
| Shutter | linear time | log₂ (stops) | Same |
| Focus | metres | **diopters** (1/m) | Blur is linear in diopters, not distance |
| White balance | kelvin | **mired** (10⁶/K) | Colour shift is linear in mired, not kelvin |

Focus and white balance both land on a **reciprocal** of the intuitive unit, and for the same
structural reason: the physical effect depends on the reciprocal quantity. Sampling linearly in the
intuitive unit spends most of the budget where nothing changes.

This is not a stylistic preference. §4 and §5 quantify what it costs to get it wrong: a 15×
variation in effective step size for white balance, and complete non-coverage of the near field for
focus.

---

## 3. ISO and shutter

Both are swept **geometrically** — each step is a constant ratio, which is a constant number of
stops. This is the standard photographic measure and needs no defence: sensor response to gain and
to integration time are both multiplicative, so a fixed ratio is a fixed perceptual and
signal-to-noise interval.

`geometricSeries(start, end, n)` produces `start · r^i` with `r = (end/start)^(1/(n-1))`.

The current defaults, 9 steps each:

```
ISO       100  141  200  283  400  566  800  1131  1600      (ratio √2, i.e. ½-stop)
Shutter   1  1.78  3.16  5.62  10  17.8  31.6  56.2  100 ms  (ratio 10^0.25, i.e. ~0.83 stop)
```

The ISO ratio of √2 is exactly a half stop, which makes the axis directly interpretable in
photographic terms.

**A shutter unit bug found and fixed.** The editor displayed exposure in milliseconds but parsed
typed input as nanoseconds — `formatValue` divided by 10⁶ with no matching multiply on the way back
in. Typing `20` produced a 20 ns request, silently clamped to the sensor's 85 µs floor, so the
sweep ran at the wrong exposure with no error anywhere. It affected the List field *and* the
Uniform From/To fields. The fix pairs every `formatValue` with an inverse `parseValue`; three unit
tests now pin the round-trip.

---

## 4. Focus — the long one

Focus took the most experimentation because, unlike ISO and shutter, the hardware imposes a
quantisation that no Camera2 characteristic reports.

### 4.1 Why diopters, not metres

Blur is `c = k · ΔD`. A 0.1 D error produces 0.85 px of blur whether it happens at infinity or at
10 cm. Distance has no such property: 10 cm of error is invisible at 20 m and catastrophic at 15 cm.

So **uniform-in-diopters is uniform-in-blur**, and it is provably the optimal covering for a blur
criterion. Everything below follows from that.

### 4.2 The CoC-limited sampling grid

Taking one pixel as the criterion — justified in §1 by the vendor's own hyperfocal:

```
ΔD = pitch / k = 2.000 µm / 17.051 µm/D = 0.1173 D
```

| Criterion | Step | Values over 0–10 D |
|---|---|---|
| 0.5 px (Nyquist on the blur kernel) | 0.0586 D | 171 |
| **1 px (gap-free)** | **0.1173 D** | **86** |
| 2 px | 0.2346 D | 43 |
| 3 px | 0.3519 D | 29 |

At 86 values, every scene distance from ∞ to 10 cm sits within half a pixel of blur of some
captured frame. That is the reference "complete survey" for this rig.

### 4.3 Probing the actuator grid

**No Camera2 key reports focus resolution.** It had to be measured.

**Method.** Build a sweep whose focus list clusters six values 0.002 D apart at each of six base
positions spread across the travel (0.0, 1.0, 2.5, 5.0, 7.5, 9.98 D) — 36 points. Run it, then
compare `requestedFocusDiopters` against `actualFocusDiopters` in the manifest. Where several
requests collapse onto one actual, the spacing of the distinct actuals *is* the grid.

**Result.** The grid is real, and it is **not uniform**:

| Base | Local grid step | Distinct of 6 |
|---|---|---|
| 0.00 D | 0.004115 D | 3 |
| 1.00 D | 0.004071 D | 4 |
| 2.50 D | 0.004005 D | 4 |
| 5.00 D | 0.003896 D | 4 |
| 7.50 D | 0.003788 D | 3 |
| 9.98 D | 0.003684 D* | 2 |

\* observed as 0.007367, exactly two steps — only alternate positions were hit at that base.

A linear fit is excellent (residuals below the measurement resolution):

```
step(D) ≈ 0.004114 − 0.00004326 · D        diopters per actuator step
```

The step **shrinks about 11 %** from infinity to closest focus. Integrating gives the total:

```
∫₀¹⁰ dD / step(D) = (1/0.00004326) · ln(0.004114 / 0.003681) ≈ 2568 distinct positions
```

In blur terms one actuator step is 0.035 px at infinity falling to 0.031 px at 10 cm — far below
both the pixel pitch and the 1.21 px diffraction floor. **The actuator is not the limiting factor
for any optically meaningful sampling**; it only matters when deliberately sampling finer than the
diffraction limit, as in a PSF study.

### 4.4 Repeatability

Resolution and determinism are different questions, and determinism matters more for a rig meant to
be re-run over months. The identical 36-point probe was run twice.

**All 36 positions returned byte-identical actuals across both runs. Worst repeat error:
0.000000 D.**

So focus is fully deterministic: the same request lands on the same physical position every time,
and a sweep can be reproduced exactly later. This is what makes recording the *requested* value
sufficient for reproduction, even though the *actual* value is what analysis must use.

### 4.5 A proposal that the probe refuted

A fixed base unit of 0.02057 D was proposed, with all focus values as integer multiples of it. The
probe refutes it:

| D | grid step | 0.02057 / step |
|---|---|---|
| 0 | 0.004114 | **5.000** |
| 4 | 0.003941 | 5.220 |
| 10 | 0.003681 | 5.588 |

It is exactly five actuator steps at infinity, and drifts to 5.59 by closest focus. Multiples of it
progressively fall off-grid. **A single global base unit cannot exist, because the grid itself is
not uniform.** The correct equivalent is to walk the measured law — `D(i+1) = D(i) + step(D(i))` —
which lands on real positions everywhere. That remains unimplemented and is listed in the README.

### 4.6 Why the shipped scheme is band-based, not CoC-based

Everything above argues for uniform-in-diopters. The shipped Uniform mode instead divides the range
into four **distance** bands — 100 m–∞, 10–100 m, 1–10 m, 0.1–1 m — each sampled geometrically in
distance, with all five boundaries always captured.

This is a deliberate departure, made for a stated reason: the goal is a mesh of images whose
**subjects** are spread across scene depth, not a blur-uniform survey. The two are very different:

| Band | Diopter span | Blur span |
|---|---|---|
| 100 m – ∞ | 0.010 D | 0.09 px |
| 10 – 100 m | 0.090 D | 0.77 px |
| 1 – 10 m | 0.900 D | 7.67 px |
| 0.1 – 1 m | 9.000 D | 76.73 px |

Equal counts per band therefore **oversample the far field by roughly three orders of magnitude**
relative to what a blur criterion would ask for. That is the intended trade, and the CoC-limited
86-value set remains reachable through List mode for optical work.

The shipped default is **0 / 1 / 2 / 2** across the four bands — ten focus distances, weighted
toward the near half where the optical content actually is. The 100 m–∞ band gets no interior
values at all, which the table above justifies: 0.09 px of blur across the whole band means its two
edges already cover everything optically distinct in it.

Two implementation notes follow from the physics:

- **Geometric in distance is geometric in diopters.** Since `D = 1/d`, the reciprocal of a geometric
  series is a geometric series. So the generator works entirely in diopters — the unit the camera
  takes — and gets geometric spacing in metres for free. A unit test asserts this property, because
  the whole design rests on it.
- **The 100 m–∞ band cannot be sampled geometrically.** It has no finite endpoint in distance. In
  diopters it is simply `[0, 0.01]`, so it is sampled linearly there. It is also physically almost
  empty: 0.01 D spans 0.09 px of blur and only about **two actuator positions**, so counts in that
  band are capped at one interior value. Asking for more returns duplicate frames.

### 4.7 A scheme that was evaluated and rejected

A proposal to make the gap very small near 0 D and grow it past 1 D at 5 D was modelled before
being declined:

| | samples | gap @ 0.5 D | gap @ 5 D | gap @ 8 D |
|---|---|---|---|---|
| growing gap (0.01 → 1.0 D at 5 D) | 31 | 0.45 px | **3.90 px** | **6.71 px** |
| uniform 0.1173 D | 87 | 0.50 px | 0.50 px | 0.50 px |

Ten of the thirty steps changed the spot size by under 2 % — optical duplicates — while past 5 D a
subject between samples was 4–7 px blurred in every captured frame. The scheme spends frames where
nothing changes and skips where everything does.

The recommendation instead was to **truncate the range rather than stretch the gaps**: covering
0–3 D uniformly at 0.1173 D takes 26 values, every one of them valid, and honestly declares that
subjects closer than 33 cm are out of scope. A range cutoff is listed as further work.

### 4.8 Verification

A 15-value Equal-preset sweep on the device: 15/15 distinct actuator positions, all `settled`,
every request within half a grid step of its target, and the tightest neighbouring pair exactly 1.0
actuator steps apart — confirming the per-band capacity caps land precisely at the hardware limit.

---

## 5. White balance

### 5.1 Mired, not kelvin

Colour temperature behaves reciprocally: a 1000 K step at 2000 K is a dramatic shift, the same step
at 9000 K is nearly invisible. The uniform measure is the **mired**, `M = 10⁶ / K`.

Measured over 2000–10000 K with nine values:

| Spacing | Result |
|---|---|
| Linear in kelvin | mired steps of 167, 83, 50, 33, 24, 18, 14, 11 — a **15× variation** |
| Linear in mired | 10000, 6667, 5000, 4000, 3333, 2857, 2500, 2222, 2000 K — constant 50 mired |

A unit test asserts the kelvin steps are *non*-uniform, so a later "simplification" to a linear
kelvin ramp fails loudly rather than silently wasting most of the budget at the blue end.

### 5.2 Deriving channel gains — and a method that failed

White balance is commanded with `CONTROL_AWB_MODE = OFF`, `COLOR_CORRECTION_MODE =
TRANSFORM_MATRIX`, an identity `COLOR_CORRECTION_TRANSFORM` (so gains are the only variable), and
explicit `COLOR_CORRECTION_GAINS`.

**The principled derivation was implemented first and abandoned.** Route: colour temperature →
Planckian locus → CIE 1931 xy (Kim et al. cubic) → XYZ → invert the sRGB primaries → gains as the
reciprocal, normalised so the smallest is 1.0.

It fails at the warm end, because the sRGB blue primary's contribution collapses there and the
reciprocal explodes:

| Kelvin | Blue gain (unclamped) |
|---|---|
| 3000 | 6.5 |
| 2800 | 8.5 |
| 2500 | 14.7 |
| 2200 | 35.2 |
| 2000 | **126.2** |
| 1700 | **~10⁹** |

With any sane clamp, **every temperature below about 2850 K produced the same blue gain** — the warm
third of any sweep collapsing into near-duplicates. A unit test asserting gain monotonicity caught
it before it shipped.

**The shipped derivation** is the Tanner Helland approximation with Neil Bartlett's refinement,
fitted directly to black-body colours rather than derived by inverting primaries. Over
2000–25000 K it is bounded (largest gain 12.8×, blue at 2000 K), strictly monotonic in R/B, and
needs no clamping. `MIN_KELVIN` is 2000 because the fit's blue term reaches zero below that.

**These gains are nominal, not colorimetric.** They are not derived from this sensor's
`SENSOR_CALIBRATION_TRANSFORM` / `SENSOR_COLOR_TRANSFORM`. Kelvin is a label on a smooth, monotonic,
repeatable family of gains — the manifest records the gains actually applied, and analysis should
use those. The device does report reference illuminants D65 and STANDARD_A with both calibration
transforms, so a colorimetric version is possible; it is listed as further work.

### 5.3 Verification

A 5-value sweep at 2000 / 2500 / 3333 / 5000 / 10000 K (constant 100-mired steps):

```
req K   applied gains (R, Ge, Go, B)        R/B     settled
 2000   1.000, 1.838, 1.838, 12.834      0.0779    True
 2500   1.000, 1.572, 1.572,  3.596      0.2781    True
 3333   1.000, 1.337, 1.337,  1.978      0.5057    True
 5000   1.000, 1.119, 1.119,  1.243      0.8044    True
10000   1.250, 1.161, 1.161,  1.000      1.2500    True
```

All five gain sets distinct and monotonic in R/B. Measured on the output JPEGs, mean R/B ran
0.676 → 0.825 → 1.046 across the sweep — the same direction, **compressed** relative to the
commanded ratio because the pipeline is tonemapped and gamma-encoded. That compression is itself
evidence for the warning in §7 about radiometric linearity.

Note the gains are normalised so the smallest is 1.0, which pins the red gain flat at 1.0 across
the warm half. The invariant that survives normalisation is the **R/B ratio**, not the individual
channels — the monotonicity test asserts on the ratio for that reason.

### 5.4 What AUTO actually means

The default. It sets `CONTROL_AWB_MODE = AUTO` with `CONTROL_AWB_LOCK = true` and touches no colour
keys.

But `CONTROL_MODE = OFF` is set for every manual capture, and per the Camera2 contract that
**overrides the individual 3A mode fields**. So the AWB algorithm is not running during a sweep.
What the frames carry is whatever balance the HAL had converged to *before* the manual request took
over, then held frozen. Observed `CONTROL_AWB_STATE` on this path is `LOCKED` (3), versus
`INACTIVE` (0) on a manual sweep — the two paths are distinguishable in the manifest.

This is stable across a sweep, which is what a sweep needs, and it is what the app did before the
axis existed, so it is preserved exactly. The practical consequence for users is that the balance
depends on what the preview was looking at when capture started.

---

## 6. Settling

After each settings change, `settleFrames` frames are captured to the preview surface and
discarded, and the result metadata is checked against the request before the kept frame.

**A false-negative rate of 12 in 90 was traced to exposure quantisation.** Sensors quantise
integration time to a line-time step — on this device the residual was around 278 ns — so an exact
equality test on `SENSOR_EXPOSURE_TIME` reported settled frames as unsettled. The check now uses a
relative tolerance:

```
tolerance = max(exposure / 200, 1000 ns)
```

Focus is checked to within 0.01 D — comfortably above the 0.0041 D actuator grid, so grid snapping
never trips it, while still catching a lens that has not arrived. `LENS_STATE` must read
`STATIONARY` where reported. Colour gains, when commanded, are checked to 0.01 on red and blue.

**Every one of these checks tolerates a null read.** Not every HAL populates every key in every
result, and a missing value must not be reported as a failure to settle.

---

## 7. Averaging, downscaling and format

**Averaging** accumulates *N* frames in ARGB and writes the mean, reducing read noise by √N. It
multiplies capture time but not file count. With JPEG the averaged result is re-encoded, adding a
second generation of loss on top of the noise just removed — the UI offers to switch to PNG.

Averaging currently **discards the individual frames**, which is exactly the per-frame variance a
photon-transfer curve needs. Listed as further work.

**Downscaling** is an integer box average — true pixel binning, which also lowers noise by the
block size. Only factors dividing both frame dimensions exactly are offered, so no sensor rows are
ever cropped; on this sensor (4080 × 3060) that is 1, 2, 3, 4, 5, 6, 10.

> **Do not downscale a focus sweep.** The Airy disk is 1.208 px, so the PSF is already at the
> sampling limit. Even 2× binning undersamples it and destroys the very measurement a focus sweep
> exists to make.

**Format.** Neither option is sensor RAW:

| | JPEG | PNG |
|---|---|---|
| Encoding | Lossy: 4:2:0 chroma subsampling, DCT quantisation | Lossless (DEFLATE) |
| Source | The camera's own encoder output | `YUV_420_888` converted to ARGB (BT.601 integer coefficients) |
| Size | ~5 MB/frame full res | ~30 MB/frame full res |

PNG is a lossless *encode*, but of already-processed 8-bit data. The sensor is 10-bit
(`whiteLevel = 1023`), and the pipeline applies tonemapping and gamma before either format sees the
data. **Pixel values are not linear in scene radiance in either case**, and the chroma subsampling
in the JPEG path has already discarded half the colour resolution before encoding.

For genuinely linear data the `RAW` capability is present and unused. That is the single largest
gap in the project.

---

## 8. What is still uncontrolled

Seven Camera2 keys remain at HAL defaults and may vary between frames. They are listed here because
they are unmeasured confounds, not because they are known to be problems:

| Key | Why it matters |
|---|---|
| `BLACK_LEVEL_LOCK` | Unlocked, so the black point may be re-estimated between frames — a direct offset error for noise work |
| `TONEMAP_MODE` / `TONEMAP_CURVE` | The device's own contrast curve, the main reason output is not radiometrically linear |
| `SHADING_MODE` | Lens shading correction is on and unrecorded, so radial falloff is being altered |
| `DISTORTION_CORRECTION_MODE` | Resamples the image — interacts badly with the PSF measurements a focus sweep is for |
| `COLOR_CORRECTION_ABERRATION_MODE` | Chromatic aberration correction, also a resample |
| `HOT_PIXEL_MODE` | Defect correction alters individual pixel values |
| `CONTROL_POST_RAW_SENSITIVITY_BOOST` | Can apply digital gain beyond `SENSOR_SENSITIVITY` |

Only ISO, exposure, focus, lens state, colour gains and AWB state are read back and recorded. The
rest are unmeasured. Pinning them is listed as further work in the README.
