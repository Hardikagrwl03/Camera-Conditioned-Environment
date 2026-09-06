# Lattice

An Android capture app for a photometric rig. It takes the camera off automatic — the whole 3A
pipeline is switched off — and sweeps a grid of settings, saving one frame per grid point together
with a record of what the sensor **actually** applied.

Four axes are swept: **ISO × shutter × focus distance × white balance**. Sessions land in
`/sdcard/Lattice/<YYYYMMDD_HHmmss>/`, one directory per sweep, each holding the frames, a
`manifest.json` and a `metadata.csv`.

- **Using the app?** Start with the [user guide](USER_GUIDE.md).
- **Design history?** Every feature has a plan in [plans/](plans/), including the deviations that
  came up during implementation and why.
- **Why is it sampled that way?** [EXPERIMENTS.md](EXPERIMENTS.md) has the optics, the hardware
  probes, the formulas, and the approaches that were measured and rejected.
- **Working on it with Claude Code?** Five project skills in [.claude/skills/](.claude/skills/)
  cover setup, on-device verification, adding a sweep axis, checking a captured session, and
  probing the camera. They carry the safety rules that this project learned the hard way.

---

## Contents

- [Setup](#setup)
- [Project layout](#project-layout)
- [How it works](#how-it-works)
- [Design decisions worth knowing](#design-decisions-worth-knowing)
- [Testing](#testing)
- [Development utilities](#development-utilities)
- [Further work](#further-work)

---

## Setup

### Requirements

| | Version |
|---|---|
| JDK | 17+ (developed on 21) |
| Gradle | 9.6.0, via the wrapper |
| Android Gradle Plugin | 9.4.0 |
| Kotlin | 2.2.10 |
| Compose BOM | 2026.02.01 |
| `compileSdk` / `targetSdk` | 37 |
| `minSdk` | **35** |

`minSdk 35` is deliberately high. This is instrument software for one known rig, not a consumer
app, so nothing is compromised to support old devices — adaptive icons are guaranteed, and the
Camera2 surfaces are used without compatibility shims.

### Device requirements

The camera should report `MANUAL_SENSOR`; without it ISO, exposure and focus cannot be commanded.
The app does not refuse to start — it surfaces a warning on the preview screen and lets you
proceed, since the preview and the plumbing still work. `MANUAL_POST_PROCESSING` is needed only
for the white balance sweep, and that editor degrades to Auto-only without it.

Developed against a Samsung SM-S711B (`FULL` hardware level, `CALIBRATED` focus distance).

### Build and run

```bash
./gradlew :app:assembleDebug        # build
./gradlew :app:installDebug         # build + install over adb
./gradlew :app:testDebugUnitTest    # unit tests
```

Two permissions are required at runtime, both handled by `PermissionGate`: `CAMERA`, and
`MANAGE_EXTERNAL_STORAGE` for writing outside app-private storage. The gate re-checks on resume, so
returning from the system settings screen is enough.

---

## Project layout

```
app/src/main/java/dev/hamster/lattice/
├── MainActivity.kt              Activity, theme, keep-screen-on
├── camera/
│   ├── CameraCapabilities.kt    Characteristics snapshot + previewDisplayAspect
│   ├── CameraController.kt      All Camera2 state: session, requests, the sweep loop
│   ├── WhiteBalance.kt          Colour-temperature maths (pure, JVM-testable)
│   ├── FrameAverager.kt         Accumulates ARGB across frames, encodes the mean
│   ├── Downscaler.kt            Integer box downscale (pixel binning)
│   ├── YuvConverter.kt          YUV_420_888 → ARGB, BT.601 integer coefficients
│   └── HandlerExecutor.kt       Executor over the camera Handler
├── model/
│   ├── SweepConfig.kt           The configuration; axis types; resolved value lists
│   ├── SweepDefaults.kt         Defaults, focus band table, banded generator, presets
│   ├── WhiteBalanceAxis.kt      WB axis and its three modes
│   └── OutputFormat.kt          JPEG | PNG
├── storage/
│   ├── SweepStorage.kt          Session dirs, filenames, manifest.json, metadata.csv
│   ├── CaptureRecord.kt         One row of per-frame truth
│   └── ConfigStore.kt           SharedPreferences persistence, schema v3 + migrations
└── ui/
    ├── MainScreen.kt            Preview, the 4×4 tile grid, capture button, progress
    ├── MainViewModel.kt         UiState machine, config hoisting, ConfigSection enum
    ├── ConfigureSheet.kt        The editing bottom sheet, Reset, Stepper
    ├── AxisEditor.kt            One editor per section; the largest UI file
    ├── ConfigSummaries.kt       Single source of truth for how a setting is described
    ├── PermissionGate.kt        Camera + all-files gate
    └── theme/                   Material 3 theme, accent palette
```

Roughly 4 100 lines of Kotlin in `main`, 54 % of it UI.

### Where to make a change

| Task | Files |
|---|---|
| Add a swept axis | `SweepConfig` (values + `totalCaptures`), `CameraController` (request key + loop), `SweepStorage` (filename, manifest, CSV), `ConfigStore` (schema bump), `ConfigSection`, an editor in `AxisEditor` |
| Change what a tile displays | `ConfigSummaries.kt` only — the tiles and the sheet headers both read from it, so they cannot disagree |
| Add a per-frame setting | `SweepConfig`, `CameraController.buildManualRequest`, `ConfigSection`, an editor |
| Change output encoding | `CameraController.encodeOutput`, `OutputFormat` |

---

## How it works

### State machine

`MainViewModel` exposes one `UiState`: `Initializing → Preview → Capturing → Finished`, plus
`Error`. Configuration is hoisted into the ViewModel alongside the camera id it was built for, so
it survives recomposition, theme changes and backgrounding.

### The capture request

`CameraController.buildManualRequest` builds every frame from `TEMPLATE_STILL_CAPTURE` with
`CONTROL_MODE = OFF`, then sets ISO, exposure, focus, frame duration, and — only when white balance
is being swept — `CONTROL_AWB_MODE = OFF` with explicit `COLOR_CORRECTION_GAINS` and an identity
transform. Noise reduction and edge enhancement are switched off when the device allows it.

### The sweep loop

```
for focus:                 # outermost: lens travel is the slowest transition
  for white balance:       # gains apply within a frame or two
    for iso:
      for shutter:         # innermost: fastest to change
        settle(n frames)   # discarded warm-up, targets the preview surface only
        capture(m frames)  # targets the ImageReader only
        average → downscale → encode → write
```

`settle()` re-reads the result metadata and confirms the sensor actually applied the request before
the kept frame; the outcome becomes the `settled` flag in the manifest.

### Persistence

`ConfigStore` writes the configuration to SharedPreferences as JSON, stamped with a fingerprint of
the camera (id plus ISO, exposure and focus ranges). A configuration is never restored onto
different hardware, where its values would be meaningless. Schema is at **v3**; v1 and v2 payloads
migrate rather than being discarded.

---

## Design decisions worth knowing

These are the ones that look wrong until you know why. Each is also commented at its site.

**Reciprocal units everywhere.** Focus is swept in diopters, not metres, because defocus blur is
linear in diopters. White balance is swept in mired, not kelvin, for the same reason — over
2 000–10 000 K, nine linear-in-kelvin steps vary by 15× in actual colour shift. `SweepConfigTest`
asserts the *non*-uniformity in kelvin so a later "simplification" fails loudly.

**Auto white balance is inherited, not live.** `CONTROL_MODE = OFF` overrides the individual 3A
mode fields, so on Auto the frames carry whatever balance the HAL had converged to before the sweep,
then frozen. That is stable across a sweep, which is what a sweep needs. It is deliberately left
exactly as it was — see §2 of [WHITE_BALANCE_PLAN.md](plans/WHITE_BALANCE_PLAN.md).

**Colour gains come from a fitted black-body curve, not the Planckian locus.** The principled route
— locus → CIE xy → invert the sRGB primaries — makes the blue gain explode at the warm end (126×
at 2 000 K, ~10⁹ at 1 700 K), so everything below ~2 850 K clamped to the same value. The
Helland/Bartlett fit stays bounded at 12.8× and strictly monotonic.

**Exposure settling uses a relative tolerance.** Sensors quantize exposure to a line-time step, so
exact equality reported 12 of 90 frames as unsettled. Tolerance is `exposure/200` with an absolute
floor.

**Downscale factors are derived from the frame size**, not hard-coded: only factors dividing both
dimensions exactly are offered, so a downscale never crops sensor rows.

**Text fields own a `TextFieldValue` buffer** keyed to a reset counter. The plain
`(String, (String) -> Unit)` overload discards the IME's cursor on every recomposition and the caret
jumps while typing. This has regressed once; don't undo it.

**Sheet layout avoids reflow under the finger.** The sheet is bottom-anchored, so any text below a
slider that changes line count shifts the control mid-drag. Text below sliders is kept at a
constant line count.

---

## Testing

45 JVM unit tests, no instrumentation required:

```bash
./gradlew :app:testDebugUnitTest
```

| Suite | Covers |
|---|---|
| `SweepConfigTest` | Geometric series, downscale factor derivation, focus band generation and clamping, shutter ms/ns round-trip, `totalCaptures` |
| `WhiteBalanceTest` | Mired conversion, uniform-in-mired spacing, gain monotonicity and bounds, the AUTO single-null invariant |

**Tests must stay free of Android types.** `android.util.Range` and friends stub out to a throwing
shim under unit test and have broken this suite before. Anything needing a `Range` belongs behind a
pure function that takes plain values — that is why `SweepDefaults` exposes pure generators
alongside the `CameraCapabilities` overloads.

`androidTest/` holds only the template stub; there is no instrumented coverage yet.

---

## Development utilities

Verification is done against a real device — the interesting behaviour is all hardware.

```bash
# session management (always diff against a snapshot; never delete by `tail -1`)
adb shell ls /sdcard/Lattice/ > before.txt
adb shell ls /sdcard/Lattice/ | grep -vxF -f before.txt      # what a run created
adb pull /sdcard/Lattice/<session>/manifest.json

# what the camera reports
adb shell dumpsys media.camera | grep -iE "awbAvailableModes|availableCapabilities"

# drive the UI, guarding every tap on the foreground app
adb shell dumpsys window | grep -m1 mCurrentFocus
adb shell uiautomator dump /sdcard/wd.xml && adb shell cat /sdcard/wd.xml
adb exec-out screencap -p > shot.png
```

These are codified as project skills — see `.claude/skills/lattice-verify` and
`lattice-session-check`. Two habits worth keeping:

**Snapshot the session list before a run and delete only by exact name.** A `tail -1` once deleted
86 MB of real capture data that a failed run had not replaced.

**Guard scripted taps on the focused package.** An incoming call once landed under a blind tap
sequence and was answered. A `mCurrentFocus` check before each tap makes stray input impossible.

The launcher icon geometry is generated and previewed by rasterizing the vector locally rather than
round-tripping through an install — see the comments in `ic_launcher_foreground.xml` for the
constraints the numbers satisfy.

---

## Further work

### Capture fidelity

- [ ] **RAW / DNG capture.** The device reports the `RAW` capability. This is the single biggest
      gap: JPEG and PNG are both processed 8-bit output, tonemapped and gamma-encoded, so pixel
      values are not linear in scene radiance and true photometry is not possible on them.
      Specified as §15 of [IMPLEMENTATION_PLAN.md](plans/IMPLEMENTATION_PLAN.md), never built.
- [ ] **Pin the remaining uncontrolled Camera2 keys.** `BLACK_LEVEL_LOCK`, `TONEMAP_MODE`,
      `SHADING_MODE`, `DISTORTION_CORRECTION_MODE`, `COLOR_CORRECTION_ABERRATION_MODE`,
      `HOT_PIXEL_MODE` and `CONTROL_POST_RAW_SENSITIVITY_BOOST` all sit at HAL defaults and can vary
      between frames. Black level and tonemap matter most for noise work.
- [ ] **Save individual frames when averaging.** Currently only the mean is written, which discards
      exactly the per-frame variance a photon-transfer curve needs.
- [ ] **Colorimetric white balance.** Derive gains from `SENSOR_CALIBRATION_TRANSFORM` and
      `SENSOR_COLOR_TRANSFORM` against the reported reference illuminants (D65 / STANDARD_A) so
      kelvin becomes a calibrated label rather than a nominal one.

### Sampling

- [ ] **Focus range cutoff.** Let the user bound the focus sweep by distance, so truncating the
      range replaces stretching the gaps.
- [ ] **CoC-limited focus generator.** One button for the gap-free set — on this rig, 86 values at
      0.117 D. The count control cannot currently reach it.
- [ ] **Grid-walk focus generator** using the measured actuator law, so every request lands on a
      real actuator position instead of being snapped.
- [ ] **Probe the actuator grid at runtime.** `focusGridStep()` hard-codes a law measured on one
      device; it only caps and warns, but it is device-specific.
- [ ] **Adaptive settle tolerance**, scaled to the size of the step just taken.

### Application

- [ ] **Instrumented tests.** No Compose UI test coverage; `androidTest/` is the template stub.
- [ ] **Resume an interrupted sweep**, using the partial manifest a cancelled run already writes.
- [ ] **In-app session browser** — currently sessions are only reachable over adb or a file manager.
- [ ] **Decide whether Reset should be global.** It is section-scoped today, which is safer but
      means no single "start over".
