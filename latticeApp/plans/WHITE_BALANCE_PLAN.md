# White Balance Plan — AUTO by default, with an optional sweep axis

Adds white balance as a fourth sweep axis. It defaults to **AUTO**, which reproduces today's
behaviour exactly and leaves frame counts unchanged. Turning AUTO off exposes **List** and
**Uniform** modes, matching the ISO / shutter / focus editors.

---

## 1. Device support — verified on this rig

Read from `adb shell dumpsys media.camera` on the SM-S711B, camera 0:

| Characteristic | Value | Consequence |
|---|---|---|
| `REQUEST_AVAILABLE_CAPABILITIES` | includes `MANUAL_POST_PROCESSING` | manual colour gains are available |
| `CONTROL_AWB_AVAILABLE_MODES` | `[0 1 2 3 5 6]` | **OFF (0)** present — required to command gains |
| `COLOR_CORRECTION_AVAILABLE_MODES` | `[0 1 2]` | `TRANSFORM_MATRIX (0)` present |
| `COLOR_CORRECTION_GAINS` | `float[4]`, a request key | settable per frame |
| `COLOR_CORRECTION_TRANSFORM` | `rational[9]`, a request key | settable per frame |
| `SENSOR_REFERENCE_ILLUMINANT1 / 2` | `D65` / `STANDARD_A` | calibration data exists for a future colorimetric upgrade |

The feature is fully viable here. The code must still gate on
`caps.supportsManualPostProcessing` (already in `CameraCapabilities`), because a device without it
can only offer AUTO.

---

## 2. What "AUTO" means today — read this before changing anything

The current still request sets **both** `CONTROL_MODE = OFF` and `CONTROL_AWB_MODE = AUTO` +
`CONTROL_AWB_LOCK = true` (`CameraController.kt:237-241`).

`CONTROL_MODE = OFF` disables the entire 3A pipeline, and per the Camera2 contract it **overrides**
the individual AE/AWB/AF mode fields. So the AWB algorithm is not actually running during a sweep.
What the frames carry is whatever white balance the HAL had converged to before the manual request
took over — inherited, then frozen.

That is a defensible state for a sweep (it is stable across frames), and the request is explicitly
to keep it. **So AUTO mode must keep setting exactly these three keys and nothing else.** Do not
"fix" this into a genuinely running AWB; that would change every existing capture's behaviour.

The plan only adds new keys on the *non*-AUTO paths.

---

## 3. The swept quantity: correlated colour temperature, uniform in mired

The axis is **CCT in kelvin**. Uniform spacing is in **mired** (micro reciprocal degrees,
`M = 10^6 / K`), not in kelvin.

This is the same argument that makes focus uniform in diopters rather than metres: the reciprocal
is the unit in which equal steps are equal physical change. Measured over 2000–10000 K:

| Spacing | Steps |
|---|---|
| Linear in kelvin, 9 values | mired steps of 167, 83, 50, 33, 24, 18, 14, 11 — a **15× variation** |
| Linear in mired, 9 values | 10000, 6667, 5000, 4000, 3333, 2857, 2500, 2222, 2000 K — constant 50 mired |

A 1000 K step at 2000 K is a dramatic colour shift; the same step at 9000 K is nearly invisible.
Uniform-in-kelvin therefore wastes most of its budget at the blue end. The editor sweeps mired and
displays kelvin.

Default range 2000–10000 K (500–100 mired), which spans candlelight to deep shade.

---

## 4. Deriving gains from a colour temperature

For each kelvin value the request sets:

```
CONTROL_AWB_MODE        = OFF
COLOR_CORRECTION_MODE   = TRANSFORM_MATRIX
COLOR_CORRECTION_TRANSFORM = identity          // isolate white balance as the only variable
COLOR_CORRECTION_GAINS  = RggbChannelVector(r, g, g, b)
```

The identity transform matters: leaving the template's own matrix in place would vary the colour
matrix and the gains together, so the sweep would no longer be a white-balance sweep.

Gains come from a black-body fit — **not** from inverting the sRGB primaries.

> **Implemented differently from the first draft of this plan, for a measured reason.** The
> principled route (Planckian locus → CIE xy → invert the sRGB primaries) misbehaves badly at the
> warm end: the blue primary's contribution collapses there, so the reciprocal explodes — a blue
> gain of **126×** at 2000 K and roughly **1e9** at 1700 K. Every temperature below about 2850 K
> then hit the clamp and produced the *same* blue gain, flattening the warm third of any sweep
> into near-duplicates. The Tanner Helland approximation with Neil Bartlett's refinement is fitted
> directly to black-body colours instead, and over 2000–25000 K it stays bounded (largest gain
> 12.8×, blue at 2000 K) and strictly monotonic in R/B with zero clamping.

1. `kelvinToLinearRgb(kelvin)` → approximate emitted RGB on a 0–255 scale.
2. Gains are the reciprocals of that triple, normalised so `min(gain) == 1.0`.
3. Clamped to 1.0 – 16.0 as a guard rail; nothing inside the supported range reaches it.

`MIN_KELVIN` is **2000**, not 1700: the fit's blue term goes to zero below 2000 K, which would make
the blue gain infinite. `MAX_KELVIN` stays 25000.

Implement in a new `camera/WhiteBalance.kt` as pure functions, so they are unit-testable without
Android types:

```kotlin
fun kelvinToMired(kelvin: Double): Double
fun miredToKelvin(mired: Double): Double
/** Approximate emitted RGB (0-255) for a black body, Helland/Bartlett fit. */
fun kelvinToLinearRgb(kelvin: Double): Triple<Double, Double, Double>
/** Normalised R, G, B channel gains for a colour temperature; min gain is 1.0. */
fun kelvinToGains(kelvin: Double): Triple<Double, Double, Double>
```

**Honesty constraints to encode as comments and UI hints:**

- These gains are **nominal, not colorimetric**. They are not derived from this sensor's
  `SENSOR_CALIBRATION_TRANSFORM` / `SENSOR_COLOR_TRANSFORM`, so the kelvin label is a
  parameterisation of a smooth, monotonic, repeatable family of gains — not a claim that the frame
  is balanced for that illuminant. A colorimetric version using the device's D65 / STANDARD_A
  calibration pair is a possible later upgrade; the reference illuminants are present.
- The manifest therefore records the **actual** gains read back from `CaptureResult`, and analysis
  should use those, not the requested kelvin.
- Gains act on raw Bayer channels *before* the colour matrix, while the output is tonemapped,
  gamma-encoded 8-bit JPEG/PNG. The relationship between a gain and the final pixel value is not
  linear.

---

## 5. Data model — `model/WhiteBalanceAxis.kt` (new file)

```kotlin
enum class WhiteBalanceMode { AUTO, LIST, UNIFORM }

/** Bounds any commanded colour temperature; outside this the Planckian fit is not valid. */
const val MIN_KELVIN = 1700.0
const val MAX_KELVIN = 25000.0

data class WhiteBalanceAxis(
    val mode: WhiteBalanceMode = WhiteBalanceMode.AUTO,
    val list: List<Double> = emptyList(),      // kelvin
    val startKelvin: Double = 2000.0,
    val endKelvin: Double = 10000.0,
    val count: Int = 5,
) {
    /**
     * The values to sweep. A null element means "leave white balance alone", i.e. the AUTO path,
     * and AUTO always yields exactly one such element so frame counts are unchanged by default.
     */
    fun values(): List<Double?> = when (mode) {
        WhiteBalanceMode.AUTO -> listOf(null)
        WhiteBalanceMode.LIST -> list.map { it.coerceIn(MIN_KELVIN, MAX_KELVIN) }
            .sorted().distinct().ifEmpty { listOf(null) }
        WhiteBalanceMode.UNIFORM -> uniformMiredSeries(startKelvin, endKelvin, count)
            .ifEmpty { listOf(null) }
    }
}

/** [n] colour temperatures spaced evenly in mired between the two endpoints, ascending in kelvin. */
fun uniformMiredSeries(startKelvin: Double, endKelvin: Double, n: Int): List<Double>
```

`SweepConfig` gains `val whiteBalance: WhiteBalanceAxis = WhiteBalanceAxis()` and:

```kotlin
val whiteBalanceValues: List<Double?> get() = whiteBalance.values()
val totalCaptures: Int
    get() = isoValues.size * exposureValuesNs.size * focusValues.size * whiteBalanceValues.size
```

Defaulting the property means every existing construction site keeps compiling and keeps its
current single-value AUTO behaviour.

---

## 6. Camera — `camera/CameraController.kt`

`buildManualRequest` takes a new `kelvin: Double?` parameter. After the existing keys:

```kotlin
if (kelvin == null || !c.supportsManualPostProcessing) {
    // Unchanged AUTO path: inherited, frozen white balance (see section 2).
    b.set(CaptureRequest.CONTROL_AWB_MODE, CameraMetadata.CONTROL_AWB_MODE_AUTO)
    b.set(CaptureRequest.CONTROL_AWB_LOCK, true)
} else {
    b.set(CaptureRequest.CONTROL_AWB_MODE, CameraMetadata.CONTROL_AWB_MODE_OFF)
    b.set(CaptureRequest.CONTROL_AWB_LOCK, false)
    b.set(CaptureRequest.COLOR_CORRECTION_MODE, CameraMetadata.COLOR_CORRECTION_MODE_TRANSFORM_MATRIX)
    b.set(CaptureRequest.COLOR_CORRECTION_TRANSFORM, IDENTITY_TRANSFORM)
    b.set(CaptureRequest.COLOR_CORRECTION_GAINS, gainsFor(kelvin))
}
```

`IDENTITY_TRANSFORM` is a `ColorSpaceTransform` of nine `Rational(1,1)/Rational(0,1)` entries, built
once as a private constant. `gainsFor` maps `kelvinToGains` into an `RggbChannelVector(r, g, g, b)` —
both green channels get the same gain, since the axis is white balance, not green-split.

**Existing lines 240-241 move inside the `if`.** Nothing else in the builder changes.

### Sweep loop

Currently `focus → iso → exposure` (`CameraController.kt:364`). White balance goes second:

```kotlin
for (focus in config.focusValues) {
    for (kelvin in config.whiteBalanceValues) {
        for (iso in config.isoValues) {
            for (exposureNs in config.exposureValuesNs) {
```

Focus stays outermost because lens movement is the slowest transition. Gains apply within a frame
or two, so white balance sits above the two electronic axes but below the mechanical one.

### Settle check

`checkSettled` (`CameraController.kt:297-300`) gains a fourth condition, active only in manual mode:

```kotlin
val actualGains = result.get(CaptureResult.COLOR_CORRECTION_GAINS)
val gainsOk = expectedGains == null || actualGains == null ||
    (abs(actualGains.red - expectedGains.red) < 0.01f && abs(actualGains.blue - expectedGains.blue) < 0.01f)
```

Tolerate a null read rather than failing on it — some HALs do not populate the key in every result,
and a missing read must not be reported as "unsettled". This mirrors how `lensState` is already
treated as optional on line 301.

---

## 7. Storage — `storage/SweepStorage.kt`

**Filename** gains a `wb` token between focus and `avg`, so the existing left-to-right ordering
(coarsest axis first) is preserved:

```
iso0400_exp020000us_fd1p00D_wb5000K_avg1_ds1_0000.jpg   (white balance swept)
iso0400_exp020000us_fd1p00D_avg1_ds1_0000.jpg           (AUTO — token omitted entirely)
```

> **Changed from the first draft.** That draft wrote `wbAUTO` on the AUTO path for a uniform
> filename shape. AUTO must be indistinguishable from the app's previous behaviour, and a renamed
> output file is an observable difference that would break existing analysis scripts for users who
> never touch this feature. The token is therefore **omitted entirely** unless white balance was
> actually swept. A parser treats it as an optional field, which is a smaller burden than every
> existing filename changing.

**Manifest** gains, per capture: `requestedKelvin` (null for AUTO), `actualColorGains`
`[r, gEven, gOdd, b]` from `CaptureResult.COLOR_CORRECTION_GAINS`, and `awbState` from
`CaptureResult.CONTROL_AWB_STATE`. The config block gains `whiteBalanceMode` and
`whiteBalanceKelvin` (the resolved list).

**CSV** gains matching columns: `requestedKelvin, gainR, gainGEven, gainGOdd, gainB, awbState`.

---

## 8. UI

### New section

`ConfigSection` (`ui/MainViewModel.kt`) gains `WHITE_BALANCE` with title "White balance", short
label "WB", and its own accent. That makes **eight** sections.

`AttributeTabs` (`ui/MainScreen.kt`) currently lays out 3 + 4. With eight it becomes **4 + 4**,
which is also more regular than the current split. Verify the tiles still fit at the narrowest
supported width — if 4-up is too tight, fall back to 3 + 3 + 2.

### Editor — `WhiteBalanceEditor` in `ui/AxisEditor.kt`

Three-way `SingleChoiceSegmentedButtonRow`: **Auto | List | Uniform**, built like the existing
two-way toggles with `icon = {}`.

- **Auto** — no inputs. A short explanation that the device's own white balance is used and frozen
  for the sweep, and that this adds no frames. Show "1 value".
- **List** — comma-separated kelvin field. Reuse the `TextFieldValue` + `remember(resetKey, mode)`
  buffer pattern **verbatim**; the plain String overload loses the IME cursor and makes the caret
  jump, which has been fixed once already and must not regress.
- **Uniform** — From / To / Steps fields, exactly like `GeometricAxisEditor`'s RANGE row, but
  spacing evenly in mired.

Presets (`FocusPresetRow`-style chips emitting a whole axis):

| Preset | Range | Values |
|---|---|---|
| Full range | 2000 – 10000 K | 9 |
| Indoor | 2700 – 4000 K | 5 |
| Daylight | 5000 – 7000 K | 5 |

`ValueChips` label each value as `"5000 K · 200 mired"` so the spacing being uniform is visible in
the chips themselves.

If `!caps.supportsManualPostProcessing`, render only the Auto option with a `Hint` explaining the
device cannot command colour gains, and coerce the stored mode to AUTO.

### Wiring

- `ConfigureSheet.kt`: a `ConfigSection.WHITE_BALANCE ->` branch, plus `resetSection` returning
  `defaults.whiteBalance`, plus a `sectionImpact` string.
- `ConfigSummaries.kt`: `sectionValue` → `"AUTO"` or the count; `sectionDetail` → `"auto"` or
  `"2000 – 10000 K"`; `sectionCount` → `whiteBalanceValues.size`; and a `kelvinLabel(k)` helper.

---

## 9. Persistence — `storage/ConfigStore.kt`

Bump `SCHEMA_VERSION` to **3**. Write the axis as an object (`mode`, `list`, `startKelvin`,
`endKelvin`, `count`).

On read, accept versions 1 and 2 as before and default a **missing** `whiteBalance` key to
`WhiteBalanceAxis()`, i.e. AUTO. That is the correct migration: every config saved before this
feature existed was captured with AUTO behaviour, so AUTO is what it should restore to. Keep the
existing v1 focus-list migration intact.

---

## 10. Tests — `app/src/test/.../` (pure Kotlin, no Android types)

The suite has been broken before by `android.util.Range` stubs, so `WhiteBalanceAxis` and the
`WhiteBalance.kt` maths must both stay free of Android imports.

New `WhiteBalanceTest.kt`:

1. `mired_roundTripsThroughKelvin` — 5000 K → 200 mired → 5000 K.
2. `uniformSeries_hasConstantMiredSteps` — 9 values over 2000–10000 K give equal mired differences.
3. `uniformSeries_isNotConstantInKelvin` — asserts the steps are *not* equal in kelvin, pinning the
   design decision so a later "simplification" to linear kelvin fails loudly.
4. `uniformSeries_includesBothEndpoints`.
5. `uniformSeries_singleValueReturnsStart`.
6. `autoMode_yieldsExactlyOneNullValue` — the property that keeps default frame counts unchanged.
7. `listMode_sortsDedupesAndClamps`.
8. `emptyListFallsBackToAuto`.
9. `gains_areNormalisedToMinimumOne` — for several temperatures, `min(r, g, b) == 1.0`.
10. `gains_moveMonotonicallyWithTemperature` — normalising to `min == 1.0` pins the red gain flat
    across the warm half, so the invariant that actually survives is the **R/B ratio**, which must
    rise with temperature. This is the real correctness check on the maths.
11. `gains_areFiniteAndBoundedEverywhereInRange` — sweeps 500 points rather than sampling a few,
    since a reciprocal blowing up is a local failure; asserts nothing reaches the 16× guard rail.
12. `gains_outsideTheRangeClampToTheEndpoints`, `neutralPointIsNearSixtySixHundredKelvin`,
    `linearRgb_staysWithinTheEightBitRange`.

Extend `SweepConfigTest.kt`:

12. `totalCaptures_multipliesByWhiteBalance` — 2 × 3 × 4 focus × 5 WB = 120.
13. `defaultConfig_isAutoAndDoesNotChangeFrameCount` — a default config's `totalCaptures` equals
    what it was before the axis existed.

---

## 11. Files touched

| File | Change |
|---|---|
| `camera/WhiteBalance.kt` | **new** — mired conversion, Planckian xy, gains |
| `model/WhiteBalanceAxis.kt` | **new** — mode enum, axis, uniform series |
| `model/SweepConfig.kt` | add `whiteBalance` (defaulted), extend `totalCaptures` |
| `model/SweepDefaults.kt` | `forCamera` supplies the default AUTO axis |
| `camera/CameraController.kt` | `kelvin` param, AWB branch, loop nesting, settle check |
| `storage/SweepStorage.kt` | filename `wb` token, manifest + CSV fields |
| `storage/ConfigStore.kt` | schema → 3, persist axis, default missing key to AUTO |
| `ui/MainViewModel.kt` | `ConfigSection.WHITE_BALANCE` |
| `ui/MainScreen.kt` | tabs 3+4 → 4+4 |
| `ui/AxisEditor.kt` | `WhiteBalanceEditor`, presets |
| `ui/ConfigureSheet.kt` | section branch, reset, impact |
| `ui/ConfigSummaries.kt` | summary, detail, count, `kelvinLabel` |
| tests | new `WhiteBalanceTest.kt`, two additions to `SweepConfigTest.kt` |

---

## 12. Verification

1. `./gradlew :app:testDebugUnitTest` — all green.
2. `./gradlew :app:installDebug`.
3. **Default is unchanged:** clear app data, launch, confirm the WB tile reads `AUTO` and the total
   frame count matches what the same ISO/shutter/focus config produced before this change.
4. Open WB → confirm Auto / List / Uniform, presets, and mired-labelled chips.
5. Select Uniform 2000–10000 K × 5; confirm chips read 2000, 2500, 3333, 5000, 10000 K — equal
   mired steps, *not* equal kelvin steps.
6. Set ISO, shutter and focus to 1 value each, so the sweep is 5 frames, one per temperature.
7. **Snapshot `adb shell ls /sdcard/FramesSweep/` before capturing.** Run the sweep.
8. Check the manifest: `actualColorGains` must differ across the five frames and move monotonically
   (red down, blue up as kelvin rises), and every frame must report `settled: true`.
9. Pull two frames from opposite ends and confirm they are visibly different in colour — this is
   the end-to-end proof that the gains reached the sensor rather than being silently ignored.
10. Switch back to Auto, re-run, confirm one frame with `requestedKelvin: null` and `wbAUTO` in the
    filename.
11. Force-stop and relaunch; confirm the WB axis persists.
12. Delete the verification sessions **by exact name**, diffing against the snapshot from step 7.

---

## 13. Notes and risks

- **The AUTO path must stay byte-for-byte identical.** Section 2 explains why it behaves the way it
  does; the only acceptable change to it is none. Verification step 3 exists specifically to catch
  a regression here.
- **Filenames are unchanged on the AUTO path** — see section 7. Only a sweep that actually varies
  white balance gains the `wb` token.
- **Frame counts multiply.** The WB axis is the fourth multiplicand; a 5-value WB sweep makes an
  already-large mesh five times larger. The sheet footer's existing frames / storage / duration
  estimate covers this automatically once `totalCaptures` is extended.
- **Colour gains are the one axis whose "actual" can silently differ from the request** if the HAL
  clamps them. That is exactly why step 8 checks the read-back values rather than trusting the
  request, and why the manifest records gains rather than only kelvin.
- Not in scope, but adjacent and worth doing later: the other uncontrolled keys —
  `BLACK_LEVEL_LOCK`, `TONEMAP_MODE`, `SHADING_MODE`, `DISTORTION_CORRECTION_MODE` — remain at HAL
  defaults and still vary between frames.
