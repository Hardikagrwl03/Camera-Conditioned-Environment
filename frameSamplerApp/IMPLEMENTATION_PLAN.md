# Frame Sampler — Implementation Plan

An Android app that runs a **parameter sweep** over ISO × exposure time × focal distance,
capturing (and optionally frame-averaging) a still at every combination into
`/sdcard/FramesSweep/YYYYMMDD_HHMMSS/`.

This document is a complete build spec. Follow the steps in order. Each step lists the
files to touch and an acceptance check.

---

## 0. Ground rules and key decisions

**Read these before writing any code — they explain *why* the design is what it is.**

1. **Use Camera2 directly, not CameraX.** The whole point of the app is manual control of
   `SENSOR_SENSITIVITY`, `SENSOR_EXPOSURE_TIME` and `LENS_FOCUS_DISTANCE`, plus the ability to
   verify from `TotalCaptureResult` that the sensor actually applied those values.
   CameraX would need `Camera2Interop` for all of it and hides the result metadata we need.
2. **Compose for UI, Camera2 on a dedicated `HandlerThread`.** All camera callbacks run on a
   background `Handler`; never touch camera objects from the main thread except to post work.
3. **Existing project facts** (do not "fix" these):
   - `namespace` / `applicationId` = `dev.hamster.framesampler`
   - `minSdk = 35`, `targetSdk = 37`, `compileSdk = 37`
   - AGP `9.4.0`, Kotlin `2.2.10`, Compose BOM `2026.02.01`
   - AGP 9 has **built-in Kotlin support**, so there is *no* `org.jetbrains.kotlin.android`
     plugin in the build files. Do not add one.
   - Gradle version catalog lives at `gradle/libs.versions.toml`. Add every new dependency
     there, never as a hard-coded coordinate in `app/build.gradle.kts`.
4. **Writing to `/sdcard/FramesSweep` requires `MANAGE_EXTERNAL_STORAGE`** ("All files access")
   on Android 11+. This is a special permission granted from a system settings screen, not a
   runtime dialog. This makes the app unpublishable on Play, which is fine — it is a lab tool.
   Implement the settings-intent flow described in Step 4.
5. **Focus distance is in diopters (1/metres), not metres.** `0.0` means infinity;
   `LENS_INFO_MINIMUM_FOCUS_DISTANCE` is the *largest* diopter value (= closest focus).
6. **Exposure time is in nanoseconds** in Camera2. The UI should speak **microseconds or
   milliseconds**; convert at the boundary and keep nanoseconds as the internal unit.
7. **Sweep size is multiplicative.** 10 × 10 × 10 = 1000 captures. Always show the user the
   frame count and an estimated duration before they commit, and always allow cancel.

---

## 1. Build files

### 1a. `gradle/libs.versions.toml`

Bump / add:

```toml
[versions]
lifecycleRuntimeKtx = "2.9.4"       # bump from 2.6.1
activityCompose = "1.11.0"          # bump from 1.8.0
coreKtx = "1.17.0"                  # bump from 1.10.1
coroutines = "1.10.2"
exifinterface = "1.4.1"

[libraries]
androidx-lifecycle-viewmodel-compose = { group = "androidx.lifecycle", name = "lifecycle-viewmodel-compose", version.ref = "lifecycleRuntimeKtx" }
kotlinx-coroutines-android = { group = "org.jetbrains.kotlinx", name = "kotlinx-coroutines-android", version.ref = "coroutines" }
androidx-exifinterface = { group = "androidx.exifinterface", name = "exifinterface", version.ref = "exifinterface" }
```

### 1b. `app/build.gradle.kts`

Add to `dependencies`:

```kotlin
implementation(libs.androidx.lifecycle.viewmodel.compose)
implementation(libs.kotlinx.coroutines.android)
implementation(libs.androidx.exifinterface)
```

**Acceptance:** `./gradlew :app:assembleDebug` succeeds.

---

## 2. Manifest

`app/src/main/AndroidManifest.xml` — add above `<application>`:

```xml
<uses-permission android:name="android.permission.CAMERA" />
<uses-permission android:name="android.permission.MANAGE_EXTERNAL_STORAGE" />

<uses-feature android:name="android.hardware.camera.any" android:required="true" />
<uses-feature android:name="android.hardware.camera.level.full" android:required="false" />
```

On `<application>` add `android:largeHeap="true"` (frame averaging holds a full-resolution
`IntArray` accumulator — see Step 8).

On `<activity android:name=".MainActivity">` add:

```xml
android:screenOrientation="portrait"
android:keepScreenOn="true"
```

Locking orientation avoids tearing down and rebuilding the capture session mid-sweep.

**Acceptance:** app installs; `adb shell dumpsys package dev.hamster.framesampler | grep MANAGE` shows the permission declared.

---

## 3. Data model — `model/SweepConfig.kt`

Package `dev.hamster.framesampler.model`.

```kotlin
enum class AxisMode { LIST, RANGE }

/** A sweep axis: either an explicit list of values, or a geometric range expanded to [count] values. */
data class GeometricAxis(
    val mode: AxisMode,
    val list: List<Double>,      // used when mode == LIST
    val start: Double,           // used when mode == RANGE
    val end: Double,
    val count: Int,
) {
    fun values(): List<Double> = when (mode) {
        AxisMode.LIST  -> list.sorted().distinct()
        AxisMode.RANGE -> geometricSeries(start, end, count)
    }
}

/** Focal distance has no geometric requirement — explicit list only (spec item 5). */
data class LinearListAxis(val list: List<Double>) {
    fun values(): List<Double> = list.sorted().distinct()
}

data class SweepConfig(
    val iso: GeometricAxis,             // ISO / sensitivity, unitless integers after rounding
    val exposure: GeometricAxis,        // exposure time in NANOSECONDS
    val focus: LinearListAxis,          // focus distance in DIOPTERS (0 = infinity)
    val framesToAverage: Int = 1,       // "n" from spec item 8
) {
    val isoValues: List<Int> get() = iso.values().map { it.roundToInt() }.distinct()
    val exposureValuesNs: List<Long> get() = exposure.values().map { it.roundToLong() }.distinct()
    val focusValues: List<Float> get() = focus.values().map { it.toFloat() }
    val totalCaptures: Int get() = isoValues.size * exposureValuesNs.size * focusValues.size
    val totalFrames: Int get() = totalCaptures * framesToAverage
}
```

### 3a. Geometric progression helper

```kotlin
/**
 * n values from [start] to [end] inclusive, each a constant ratio apart.
 * Requires start > 0 and end > 0. n == 1 returns [start].
 */
fun geometricSeries(start: Double, end: Double, n: Int): List<Double> {
    require(start > 0.0 && end > 0.0) { "geometric series needs positive endpoints" }
    if (n <= 1) return listOf(start)
    val ratio = (end / start).pow(1.0 / (n - 1))
    return (0 until n).map { start * ratio.pow(it.toDouble()) }
}
```

### 3b. Defaults (spec item 6 — "10 values each, covering the effects uniformly")

Defaults must be derived from the **actual device capabilities**, not hard-coded. Build them in
a `SweepDefaults.forCamera(caps: CameraCapabilities): SweepConfig`:

- **ISO** — `GeometricAxis(RANGE, start = sensitivityRange.lower, end = sensitivityRange.upper, count = 10)`.
  Geometric spacing over ISO is exactly "uniform in stops".
- **Exposure** — `GeometricAxis(RANGE, start = max(exposureRangeNs.lower, 100_000L /*100 µs*/),
  end = min(exposureRangeNs.upper, 500_000_000L /*500 ms*/), count = 10)`.
  Clamping the top end keeps the default sweep from taking many minutes; the user can raise it
  in the configure sheet up to the true device maximum.
- **Focus** — 10 values **linear in diopters** from `0.0` (infinity) to
  `LENS_INFO_MINIMUM_FOCUS_DISTANCE` (closest). Linear-in-diopters is the perceptually uniform
  spacing for defocus, which is why the spec does not ask for a geometric progression here.
  If the device reports minimum focus distance `0.0` (fixed-focus lens), emit `listOf(0.0)`
  and disable the focus section in the UI with an explanatory note.
- **framesToAverage** = 1.

**Acceptance:** unit test in `app/src/test/.../SweepConfigTest.kt` asserting
`geometricSeries(100.0, 3200.0, 6) == [100, 200, 400, 800, 1600, 3200]` within 1e-6, and that
`totalCaptures` for the default config is 1000.

---

## 4. Permissions — `PermissionGate` in `ui/`

Two permissions with two different flows:

1. **CAMERA** — normal runtime permission via
   `rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission())`.
2. **All files access** — check `Environment.isExternalStorageManager()`. If false, show a
   button that fires:
   ```kotlin
   Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
          Uri.parse("package:${context.packageName}"))
   ```
   Re-check `Environment.isExternalStorageManager()` in `ON_RESUME` (via
   `LifecycleEventObserver`) because the user returns from a settings screen with no result.

Render a simple gate screen listing which permission is missing and a grant button. Only when
both are satisfied does `MainScreen` mount.

**Acceptance:** on a fresh install the gate appears; after granting both, the preview starts
without needing an app restart.

---

## 5. Camera capabilities — `camera/CameraCapabilities.kt`

Query once at camera-open time and hold in a data class:

```kotlin
data class CameraCapabilities(
    val cameraId: String,
    val sensitivityRange: Range<Int>,          // SENSOR_INFO_SENSITIVITY_RANGE
    val exposureTimeRangeNs: Range<Long>,      // SENSOR_INFO_EXPOSURE_TIME_RANGE
    val minFocusDistanceDiopters: Float,       // LENS_INFO_MINIMUM_FOCUS_DISTANCE (0 => fixed focus)
    val hyperfocalDistanceDiopters: Float,     // LENS_INFO_HYPERFOCAL_DISTANCE
    val maxFrameDurationNs: Long,              // SENSOR_INFO_MAX_FRAME_DURATION
    val supportsManualSensor: Boolean,         // REQUEST_AVAILABLE_CAPABILITIES contains MANUAL_SENSOR
    val supportsManualPostProcessing: Boolean,
    val hardwareLevel: Int,                    // INFO_SUPPORTED_HARDWARE_LEVEL
    val activeArraySize: Rect,
    val largestJpegSize: Size,
    val previewSize: Size,
)
```

**Camera selection:** iterate `cameraManager.cameraIdList`, prefer the first `LENS_FACING_BACK`
camera whose `REQUEST_AVAILABLE_CAPABILITIES` contains
`REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR`. If none has manual sensor support, fall back to
the first back camera and surface a persistent warning banner: *"This camera does not support
manual sensor control; sweep values may be ignored by the device."*

**Preview size:** from `StreamConfigurationMap.getOutputSizes(SurfaceHolder::class.java)`, pick
the largest size that is ≤ 1920×1080 **and** has the same aspect ratio (within 0.01) as
`largestJpegSize`. Matching aspect ratio keeps the preview framing honest relative to what gets
saved.

**Capture size:** the largest `getOutputSizes(ImageFormat.JPEG)` entry.

**Acceptance:** log the whole capabilities object on open; verify on a real device that the
ranges are non-null and plausible.

---

## 6. Camera controller — `camera/CameraController.kt`

The single owner of all Camera2 state. Not a Composable, not a ViewModel — a plain class the
ViewModel holds.

### 6a. Threading

```kotlin
private val cameraThread = HandlerThread("camera").apply { start() }
private val cameraHandler = Handler(cameraThread.looper)
private val imageThread = HandlerThread("imageReader").apply { start() }
private val imageHandler = Handler(imageThread.looper)
```

Wrap the callback-based Camera2 API in `suspendCancellableCoroutine` so the sweep can be
written as straight-line `suspend` code:

- `suspend fun openCamera(id: String): CameraDevice`
- `suspend fun createSession(device, surfaces): CameraCaptureSession`
- `suspend fun captureOne(request: CaptureRequest): TotalCaptureResult`

### 6b. Session creation

Use the modern API — `createCaptureSession(List<Surface>, ...)` is deprecated:

```kotlin
val outputs = listOf(OutputConfiguration(previewSurface), OutputConfiguration(jpegReader.surface))
val config = SessionConfiguration(
    SessionConfiguration.SESSION_REGULAR, outputs,
    HandlerExecutor(cameraHandler), stateCallback
)
device.createCaptureSession(config)
```

`ImageReader` for JPEG: `ImageReader.newInstance(w, h, ImageFormat.JPEG, /*maxImages=*/ 4)`.
Keep `maxImages` small and **always** `close()` every `Image` — a leaked `Image` stalls the
whole pipeline after `maxImages` frames, which is the single most common Camera2 bug.

### 6c. Preview request (spec item 1 — "default settings")

Template `TEMPLATE_PREVIEW`, target = preview surface only, fully automatic:

```kotlin
set(CONTROL_MODE, CONTROL_MODE_AUTO)
set(CONTROL_AE_MODE, CONTROL_AE_MODE_ON)
set(CONTROL_AF_MODE, CONTROL_AF_MODE_CONTINUOUS_PICTURE)
set(CONTROL_AWB_MODE, CONTROL_AWB_MODE_AUTO)
```

Then `session.setRepeatingRequest(...)`.

### 6d. Manual sweep request builder

```kotlin
fun buildManualRequest(
    session: CameraCaptureSession,
    targets: List<Surface>,
    isoValue: Int,
    exposureNs: Long,
    focusDiopters: Float,
): CaptureRequest {
    val b = session.device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE)
    targets.forEach { b.addTarget(it) }
    b.set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_OFF)
    b.set(CaptureRequest.CONTROL_AE_MODE, CameraMetadata.CONTROL_AE_MODE_OFF)
    b.set(CaptureRequest.CONTROL_AF_MODE, CameraMetadata.CONTROL_AF_MODE_OFF)
    b.set(CaptureRequest.CONTROL_AWB_MODE, CameraMetadata.CONTROL_AWB_MODE_AUTO)
    b.set(CaptureRequest.CONTROL_AWB_LOCK, true)         // keep colour constant across the sweep
    b.set(CaptureRequest.SENSOR_SENSITIVITY, isoValue)
    b.set(CaptureRequest.SENSOR_EXPOSURE_TIME, exposureNs)
    b.set(CaptureRequest.LENS_FOCUS_DISTANCE, focusDiopters)
    // Frame duration MUST be >= exposure time or the HAL silently clips the exposure.
    b.set(CaptureRequest.SENSOR_FRAME_DURATION, min(maxFrameDurationNs, exposureNs + 10_000_000L))
    if (caps.supportsManualPostProcessing) {
        b.set(CaptureRequest.NOISE_REDUCTION_MODE, CameraMetadata.NOISE_REDUCTION_MODE_OFF)
        b.set(CaptureRequest.EDGE_MODE, CameraMetadata.EDGE_MODE_OFF)
    }
    b.set(CaptureRequest.JPEG_QUALITY, 100.toByte())
    return b.build()
}
```

**Clamp every value** to the device ranges before setting it, and record the clamped value in
the manifest so the user can see what was actually requested.

### 6e. Settling — the part that is easy to get wrong

A single `capture()` right after changing ISO/exposure/focus frequently comes back with the
*previous* settings still applied. For every configuration:

1. Issue `settleFrames` warm-up captures (default **2**) targeting the **preview surface only**
   (cheap, no JPEG encode) with the same manual request.
2. On each result check:
   - `result.get(SENSOR_SENSITIVITY) == isoValue`
   - `result.get(SENSOR_EXPOSURE_TIME) == exposureNs`
   - `abs(result.get(LENS_FOCUS_DISTANCE) - focusDiopters) < 0.01f`
   - `result.get(LENS_STATE) == LENS_STATE_STATIONARY`
3. If not all satisfied, capture up to 8 more warm-up frames before giving up. On give-up,
   proceed anyway but flag the row in the manifest with `settled: false`.
4. Only then issue the `framesToAverage` real captures targeting the JPEG `ImageReader`.

### 6f. Sweep loop and ordering

```
for (focus in focusValues)          // outermost — lens actuator is the slowest to settle
    for (iso in isoValues)
        for (exposureNs in exposureValuesNs)   // innermost
            settle(); capture n frames; average; write
```

Before the loop: `session.stopRepeating()` and `session.abortCaptures()`. After the loop (or on
cancel/error): restore the auto preview repeating request from 6c.

Cancellation: the sweep is a `suspend fun` running in `viewModelScope`; the Cancel button calls
`job.cancel()`. Wrap the loop body so cancellation still runs the preview-restore in a
`finally` block.

**Acceptance:** with a 2×2×2 test config, logcat shows each `TotalCaptureResult` reporting
exactly the requested ISO / exposure / focus.

---

## 7. Storage — `storage/SweepStorage.kt`

```kotlin
private val root = File(Environment.getExternalStorageDirectory(), "FramesSweep")

fun createSessionDir(): File {
    val name = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
    return File(root, name).apply { mkdirs() }
}
```

### Filename format (spec item 8 — "image name also storing the configuration")

```
iso<ISO>_exp<EXPOSURE_US>us_fd<FOCUS_DIOPTERS>D_avg<N>_<INDEX>.jpg
```

- `INDEX` is zero-padded to 4 digits in sweep order, so files sort in capture order.
- `FOCUS_DIOPTERS` uses `%.2f` with the `.` replaced by `p` (`fd2p50D`) so the only dot in the
  name is the extension separator — this keeps downstream shell/Python glob parsing trivial.
- Example: `iso0800_exp015000us_fd2p50D_avg4_0037.jpg`

### Manifest

Write `manifest.json` in the session dir with: app version, camera id, the full
`CameraCapabilities` dump, the resolved `SweepConfig` (all expanded axis values), and one entry
per capture containing `{ index, filename, requestedIso, actualIso, requestedExposureNs,
actualExposureNs, requestedFocus, actualFocus, framesAveraged, settled, timestampNs }`.
Pull the "actual" values from the `TotalCaptureResult` of the **first** real (non-warm-up)
capture of that configuration.

Also write `metadata.csv` with the same per-capture rows — it is what the analysis scripts will
actually read.

**Media scanner:** after the sweep, call
`MediaScannerConnection.scanFile(context, arrayOf(sessionDir.path), null, null)` so the files
show up over MTP without a reboot.

**Acceptance:** `adb shell ls /sdcard/FramesSweep/<ts>/` lists exactly `totalCaptures` JPEGs
plus `manifest.json` and `metadata.csv`.

---

## 8. Frame averaging — `camera/FrameAverager.kt`

Spec item 8: each saved image is the average of `n` frames captured at the same configuration.

```kotlin
class JpegAverager(private val width: Int, private val height: Int) {
    private val acc = IntArray(width * height * 3)
    private var count = 0

    fun add(jpegBytes: ByteArray) { /* decode ARGB_8888, accumulate R,G,B */ }
    fun result(): Bitmap { /* divide by count, build Bitmap */ }
}
```

Implementation notes:

- **n == 1 is a fast path:** write the JPEG bytes straight to disk with no decode/re-encode.
  This is the default and must not lose quality.
- **n > 1:** decode each frame with `BitmapFactory.decodeByteArray` into `ARGB_8888`, read
  pixels with `bitmap.getPixels(...)` into a reusable `IntArray`, accumulate channels, then
  `bitmap.recycle()` immediately. Divide with **rounding** (`(sum + count/2) / count`), not
  truncation. Re-encode with `Bitmap.CompressFormat.JPEG` at quality 100.
- **Memory:** a 12 MP accumulator is `12e6 × 3 × 4 B ≈ 144 MB`. That is why the manifest sets
  `largeHeap="true"`. Allocate **one** `JpegAverager` for the whole sweep and reset it between
  configurations rather than allocating per configuration.
- **Caveat to surface in the UI** (one line under the "frames to average" field):
  *"Averaging is done on gamma-encoded JPEG pixels, so it reduces noise but is not
  radiometrically linear."* If the user needs linear averaging, that requires the RAW path in
  the stretch section below.
- Copy the EXIF of the first source frame onto the averaged output using
  `androidx.exifinterface.media.ExifInterface`, then overwrite `TAG_ISO_SPEED_RATINGS` and
  `TAG_EXPOSURE_TIME` with the swept values.

**Acceptance:** with `n = 8` pointed at a static scene, the saved image is visibly less noisy
than an `n = 1` capture at the same high ISO.

---

## 9. ViewModel — `ui/MainViewModel.kt`

```kotlin
sealed interface UiState {
    data object Initializing : UiState
    data class Preview(val config: SweepConfig, val caps: CameraCapabilities) : UiState
    data class Capturing(
        val done: Int, val total: Int,
        val currentIso: Int, val currentExposureNs: Long, val currentFocus: Float,
        val sessionDirName: String,
    ) : UiState
    data class Error(val message: String) : UiState
}
```

The ViewModel:

- owns the `CameraController`,
- holds `configureSheetVisible: Boolean` and a **draft** `SweepConfig` that is only committed to
  the live config when the user taps *Apply* (so Cancel discards edits),
- exposes `startSweep()`, `cancelSweep()`, `openConfigure()`, `applyConfig()`, `resetDefaults()`,
- on sweep completion **or** cancellation, emits `UiState.Preview` again and restarts the auto
  preview request — this is spec item 9 ("app must be reset to the initial page").

Close the camera in `onCleared()` and on `ON_STOP`; reopen on `ON_START`.

---

## 10. Main screen — `ui/MainScreen.kt`

Layout (portrait):

- **Full-bleed preview.** `AndroidView` wrapping a `SurfaceView`. In its `SurfaceHolder.Callback`,
  call `holder.setFixedSize(previewSize.width, previewSize.height)` in `surfaceCreated`, then
  hand the `Surface` to the ViewModel. Tear down the session in `surfaceDestroyed`.
  Size the `SurfaceView` with an aspect-ratio modifier so the preview is not stretched.
- **Top-left: small "Configure" outlined button** (spec item 2 — explicitly *small*). Next to it,
  a compact one-line summary chip: `10 ISO × 10 exp × 10 FD × 1 = 1000 frames`.
- **Bottom-centre: large circular "Capture" button** (spec item 7).
- **Warning banner** at the top if the camera lacks `MANUAL_SENSOR`.

When `UiState.Capturing`, overlay a scrim with:

- `LinearProgressIndicator(progress = done / total)`
- `"Capturing 37 / 1000"`
- the current configuration in human units: `ISO 800 · 15.0 ms · 2.50 D (0.40 m)`
- the session folder name
- a **Cancel** button.

Estimate and show remaining time as
`(total - done) × (framesToAverage × (exposureSec + 0.15 s overhead))`.

**Acceptance:** tapping Capture immediately shows the overlay; tapping Cancel returns to the
live preview within a second or two.

---

## 11. Configure overlay — `ui/ConfigureSheet.kt`

Spec item 2/6: an **overlay screen** over the preview. Use a full-screen
`Dialog(properties = DialogProperties(usePlatformDefaultWidth = false))` containing a scrollable
`Column`, with a top app bar holding *Cancel*, the title, and *Apply*.

Three axis sections plus a global section.

### 11a. ISO section (spec item 3)

- A `SegmentedButton` row: **List** | **Range (geometric)**.
- **List mode:** a text field taking comma-separated integers, plus chips for each parsed value
  with an ✕ to remove. Show a parse error inline instead of silently dropping bad tokens.
- **Range mode:** three fields — *Start ISO*, *End ISO*, *Count*. Below them, render the
  resulting series as read-only chips so the user sees exactly what will be swept.
- Clamp start/end to `caps.sensitivityRange` and show the device range as helper text
  (`Device supports 50 – 12800`).

### 11b. Exposure section (spec item 4)

Identical structure to ISO, but:

- Values are entered in **milliseconds** (accept decimals, e.g. `0.125`), stored as nanoseconds.
- Helper text shows the device range converted to ms.
- Under the resulting chips, show the total exposure budget for the axis so the user sees when a
  sweep is going to take five minutes.

### 11c. Focal distance section (spec item 5 — list only)

- No mode toggle; list entry only.
- Entered in **diopters**, with each chip annotated with the equivalent distance:
  `2.50 D (0.40 m)`, and `0.00 D (∞)` for zero.
- Provide a "Generate N evenly spaced" helper row (N field + button) that fills the list with
  `N` values linear in diopters from `0` to `caps.minFocusDistanceDiopters` — this is how the
  default is produced, exposed as a button so the user can regenerate it after editing.
- If `caps.minFocusDistanceDiopters == 0f`, disable the section and show
  *"This camera has a fixed-focus lens."*

### 11d. Global section

- **Frames to average (n)** — integer field, default `1`, min `1`, max `64`, with the
  gamma-encoding caveat from Step 8 as helper text.
- **Settle frames** — integer field, default `2`, min `0`, max `10`, helper text
  *"Warm-up frames discarded after each settings change."*
- **Reset to defaults** button — recomputes `SweepDefaults.forCamera(caps)`.

### 11e. Footer summary

Pinned above the bottom edge, always visible:

```
10 ISO × 10 exposures × 10 focus distances = 1000 captures
× 1 frame each = 1000 frames · est. 4 min 12 s
```

Disable *Apply* when any axis resolves to zero values.

**Acceptance:** switching an axis between List and Range preserves the other axes; Cancel
discards edits; Apply updates the summary chip on the main screen.

---

## 12. Error handling and edge cases

Handle each of these explicitly — they are the ones that will actually bite:

1. **Camera in use / `CameraAccessException`** — show `UiState.Error` with a Retry button.
2. **Device ignores manual values.** If the settle check in 6e fails for > 20 % of
   configurations, finish the sweep but show a summary warning at the end:
   *"142 of 1000 captures did not reach the requested settings — see manifest.json."*
3. **Free space.** Before starting, estimate `totalCaptures × 5 MB` and compare against
   `StatFs(root.path).availableBytes`. Refuse to start with a clear message if short.
4. **Long exposures.** Any single exposure over ~2 s will make the UI look frozen; the progress
   overlay must keep updating per-configuration, not per-batch.
5. **Screen off / app backgrounded mid-sweep.** `keepScreenOn` covers the common case. On
   `ON_STOP` during a sweep, cancel it cleanly and keep the partial output (the manifest records
   how many rows were written).
6. **`ImageReader` starvation.** If `acquireNextImage()` returns null, log and retry once; never
   block the camera handler thread waiting on it.
7. **Duplicate axis values.** Geometric series with a small range and a large count produces
   repeated integers after rounding ISO — always `distinct()` after rounding, and show the
   deduplicated count in the summary so the arithmetic on screen is honest.

---

## 13. Final file layout

```
app/src/main/java/dev/hamster/framesampler/
├── MainActivity.kt                  # permission gate + FrameSamplerTheme + MainScreen
├── model/
│   ├── SweepConfig.kt               # axes, config, geometricSeries()
│   └── SweepDefaults.kt             # forCamera(caps)
├── camera/
│   ├── CameraCapabilities.kt        # query + camera selection
│   ├── CameraController.kt          # open/session/preview/sweep, suspend wrappers
│   └── FrameAverager.kt             # n-frame averaging
├── storage/
│   └── SweepStorage.kt              # session dir, filenames, manifest.json, metadata.csv
└── ui/
    ├── MainViewModel.kt             # UiState, config draft, sweep orchestration
    ├── MainScreen.kt                # preview + Configure + Capture + progress overlay
    ├── ConfigureSheet.kt            # full-screen configure dialog
    ├── AxisEditor.kt                # reusable list/range editor composable
    └── theme/                       # unchanged
```

Delete the placeholder `Greeting` / `GreetingPreview` composables from `MainActivity.kt`.

---

## 14. Build order (suggested commit sequence)

1. Build files + manifest + permission gate → app launches, asks for both permissions.
2. `CameraCapabilities` + `CameraController.openCamera` + preview → live preview on screen.
3. `SweepConfig` + defaults + unit tests → summary chip shows `1000 frames`.
4. `ConfigureSheet` + `AxisEditor` → config round-trips through Apply/Cancel.
5. `SweepStorage` + single manual capture at one fixed configuration → one correctly named JPEG
   lands in `/sdcard/FramesSweep/<ts>/`.
6. Full sweep loop + settling + progress overlay + cancel → full sweep works with `n = 1`.
7. `FrameAverager` + `n > 1` → averaging works.
8. `manifest.json` / `metadata.csv` + media scan + error cases from Step 12.

Verify each step on a physical device — the emulator's camera does not implement manual sensor
control, so ISO/exposure/focus will be silently ignored there.

---

## 15. Stretch (implement only after everything above works)

**Linear RAW averaging.** Averaging JPEGs is fine for noise reduction but not radiometrically
linear. If the camera reports `REQUEST_AVAILABLE_CAPABILITIES_RAW`, add an output-format toggle
to the configure sheet:

- Add a second `ImageReader` with `ImageFormat.RAW_SENSOR` at `getOutputSizes(RAW_SENSOR)[0]`.
- Read plane 0 as a `ShortBuffer`; accumulate as **unsigned** (`v.toInt() and 0xFFFF`) into an
  `IntArray` of `w × h` — one channel only, so ~48 MB for 12 MP.
- Divide, write back into a fresh `ByteBuffer`, and save with
  `DngCreator(characteristics, totalCaptureResult).writeByteBuffer(outputStream, size, buffer, 0)`.
- Filenames get a `.dng` extension; everything else is unchanged.

Do not start this until Steps 1–14 are complete and verified.
