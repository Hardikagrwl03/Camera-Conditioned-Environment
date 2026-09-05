# Frame Sampler — UI Fix Plan

Four defects to fix, all confirmed on the connected device (Samsung SM-S711B, Android 16,
1080×2340 screen, currently in **dark mode**):

1. **Preview geometry is wrong** — the 4:3 sensor image is squeezed into a landscape box.
2. **Theme does not follow the device** — the app renders light-on-white while the device is in dark mode.
3. **Capture button is invisible** — white circle on a white background.
4. **UI is cluttered** — the top bar collides with the status bar and wastes the screen.

Every step below lists the exact file, the change, and a check that proves it worked.

---

## 0. Measured facts (do not re-derive; these came off the device)

Run these yourself if you want to confirm before starting:

```
adb shell wm size                                    # Physical size: 1080x2340
adb shell dumpsys media.camera | grep -i orientation # Orientation: 90
adb shell cmd uimode night                           # Night mode: yes
adb shell uiautomator dump /sdcard/wd.xml && adb shell cat /sdcard/wd.xml \
  | grep -o 'class="android.view.SurfaceView"[^/]*bounds="\[[0-9,]*\]\[[0-9,]*\]"'
                                                     # bounds=[0,0][1080,810]   <-- the bug
```

| Fact | Value | Why it matters |
|---|---|---|
| Screen | 1080 × 2340 (portrait, aspect 0.462) | The activity is locked to portrait. |
| `SENSOR_ORIENTATION` | **90°** | The sensor is mounted rotated; sensor sizes are landscape. |
| `previewSize` | 1440 × 1080 (landscape 4:3) | Camera2 reports sizes in **sensor** coordinates. |
| `largestJpegSize` | 4080 × 3060 (landscape 4:3) | Same convention. |
| SurfaceView actual bounds | **1080 × 810** | Wrong. Should be **1080 × 1440**. |
| Device night mode | **on** | The app ignores it and shows white. |

**The core insight for issue 1:** Camera2 reports `previewSize` in sensor coordinates
(1440×1080, landscape). With `SENSOR_ORIENTATION = 90`, the preview is rotated 90° for display, so
on screen it occupies a **portrait 3:4** box. The current code lays the `SurfaceView` out at
`aspectRatio(1440f/1080f)` = 1.333 (landscape), so the rotated portrait image gets squashed into a
landscape box. **Aspect must be swapped when the sensor is mounted at 90° or 270°.**

---

## 1. Make the theme follow the device (fixes the white background)

### 1a. `app/src/main/res/values/themes.xml` — the actual cause

The window background comes from the platform theme, not from Compose. It is currently pinned to
**Light**, which is why the app is white while the device is in dark mode.

```xml
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <style name="Theme.FrameSampler" parent="android:Theme.DeviceDefault.DayNight.NoActionBar" />
</resources>
```

`DeviceDefault` picks up the OEM (One UI) styling, and `DayNight` follows the system dark-mode
setting — together this is what "same theme as the device" means. (`android:Theme.Material.DayNight.NoActionBar`
also works if you prefer stock Material over OEM styling.)

### 1b. `MainActivity.kt` — paint a themed background

Nothing currently draws a themed background behind the content, so the bare window shows through.
Wrap the app in a `Surface`:

```kotlin
setContent {
    FrameSamplerTheme {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = MaterialTheme.colorScheme.background,
        ) {
            FrameSamplerApp()
        }
    }
}
```

Imports: `androidx.compose.material3.Surface`, `androidx.compose.material3.MaterialTheme`,
`androidx.compose.foundation.layout.fillMaxSize`, `androidx.compose.ui.Modifier`.

### 1c. Leave `Theme.kt` alone

It already does the right thing: `dynamicColor = true` with `dynamicLightColorScheme` /
`dynamicDarkColorScheme` on Android 12+, so Compose colors already track the device's Material You
palette. The only reason it looked wrong was 1a + 1b. **Do not** replace it with a hardcoded scheme.

### 1d. Purge hardcoded colors from `MainScreen.kt`

These override the theme and are the reason the UI ignores dark mode. Replace every one:

| Current | Replace with |
|---|---|
| `color = Color.White` (status/overlay text) | `color = MaterialTheme.colorScheme.onSurface` |
| `color = Color.Gray` (session dir text) | `color = MaterialTheme.colorScheme.onSurfaceVariant` |
| `Color.Black.copy(alpha = 0.72f)` (scrims) | `MaterialTheme.colorScheme.scrim.copy(alpha = 0.72f)` |
| `color = Color.White` (capture button) | see step 3 |

Text drawn **on top of the camera preview** is the one exception — it must stay light regardless of
theme because the preview behind it is an arbitrary photo. Use `Color.White` there deliberately,
with a scrim behind it, and add a comment saying why.

**Check:** relaunch with the device in dark mode → app chrome is dark. Then
`adb shell cmd uimode night no` → app chrome turns light. Restore with `adb shell cmd uimode night yes`.

---

## 2. Fix the preview aspect ratio (the 4:3 → 3:4 squeeze)

### 2a. `camera/CameraCapabilities.kt` — capture the sensor orientation

It is not read anywhere today. Add the field:

```kotlin
data class CameraCapabilities(
    ...
    val sensorOrientation: Int,   // SENSOR_ORIENTATION: 0/90/180/270
    ...
)
```

and populate it in `CameraCapabilitiesReader.read()`:

```kotlin
val sensorOrientation = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0
```

Add it to the `CameraCapabilities(...)` constructor call and to the `manifest.json` camera block in
`SweepStorage.writeManifest` (`put("sensorOrientation", caps.sensorOrientation)`) — analysis scripts
need it to interpret image orientation.

### 2b. Add a display-aspect helper

Put this next to the data class so both the UI and any future code share one definition:

```kotlin
/**
 * Aspect ratio (width / height) the preview occupies **on screen**, as opposed to in sensor
 * coordinates. Camera2 reports sizes in sensor coordinates, which are landscape on a sensor
 * mounted at 90 or 270 degrees, so the ratio must be inverted for a portrait display.
 *
 * The activity is locked to portrait, so display rotation is always 0 here. If portrait lock is
 * ever removed, use the full relative rotation instead:
 *   (sensorOrientation - displayRotationDegrees + 360) % 360
 */
val CameraCapabilities.previewDisplayAspect: Float
    get() {
        val w = previewSize.width.toFloat()
        val h = previewSize.height.toFloat()
        return if (sensorOrientation == 90 || sensorOrientation == 270) h / w else w / h
    }
```

For this device: `1080f / 1440f = 0.75` → a portrait 3:4 box, i.e. **1080 × 1440** on screen.

### 2c. `ui/MainScreen.kt` — use it in `CameraPreviewSurface`

```kotlin
val aspect = previewCaps?.previewDisplayAspect ?: (3f / 4f)
```

Replace the current `previewCaps?.let { it.previewSize.width.toFloat() / it.previewSize.height.toFloat() }`.
Note the fallback also changes from `9f/16f` to `3f/4f`, matching the 4:3 sensor this app targets.

**Keep `holder.setFixedSize(previewSize.width, previewSize.height)` exactly as it is** — the surface
*buffer* must stay in sensor coordinates (1440×1080) because Camera2 requires a buffer size that
matches a supported output size. Only the *view* aspect changes. Changing setFixedSize will break
session configuration.

### 2d. Letterbox the preview on black, centered

Fit (not crop) is the right call for a measurement tool: the operator must see exactly the frame
that will be saved. A 3:4 preview on a 0.462 screen leaves ~900 px below — step 4 turns that dead
space into the control bar.

```kotlin
Box(
    modifier = Modifier
        .fillMaxWidth()
        .background(Color.Black),          // letterbox bars, deliberately not themed
    contentAlignment = Alignment.Center,
) {
    AndroidView(
        modifier = Modifier.fillMaxWidth().aspectRatio(aspect),
        factory = { ... },                 // unchanged
    )
}
```

**Check:**

```
adb shell uiautomator dump /sdcard/wd.xml && adb shell cat /sdcard/wd.xml \
  | grep -o 'class="android.view.SurfaceView"[^/]*bounds="\[[0-9,]*\]\[[0-9,]*\]"'
```
Must now report **`[0,0][1080,1440]`** (was `[0,0][1080,810]`). Point the camera at something with a
known circular shape and confirm it is round, not oval.

---

## 3. Make the capture button visible

It is currently `Surface(color = Color.White)` sitting on a white background — literally white on
white. It also floats in the empty area below the preview rather than in a real control bar.

Replace `CaptureButton` with a shutter control that has guaranteed contrast in both themes:

```kotlin
@Composable
private fun CaptureButton(
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    Box(
        modifier = modifier
            .size(76.dp)
            .clip(CircleShape)
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .border(3.dp, MaterialTheme.colorScheme.onSurface, CircleShape)
            .clickable(enabled = enabled, onClick = onClick)
            .semantics { contentDescription = "Start capture sweep" },
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .size(58.dp)
                .clip(CircleShape)
                .background(MaterialTheme.colorScheme.primary),
        )
    }
}
```

Requirements this satisfies, keep them if you restyle:

- An outer ring in `onSurface` and an inner disc in `primary` — both are contrast-guaranteed against
  `surface` in light *and* dark, so it can never disappear again.
- Placed in the themed control bar (step 4), not over the preview and not in a white void.
- Has a `contentDescription`, so `uiautomator dump` can find it by name instead of by pixel guessing.

**Check:** the button is clearly visible in both modes, and

```
adb shell uiautomator dump /sdcard/wd.xml && adb shell cat /sdcard/wd.xml | grep -o 'content-desc="Start capture sweep"[^/]*bounds="\[[0-9,]*\]\[[0-9,]*\]"'
```
returns bounds inside the bottom control bar.

---

## 4. Declutter the layout

### 4a. The clutter, specifically

From the current screenshot: the "Configure" button and the summary chip are drawn **underneath the
status bar**, overlapping the clock and the wifi/battery icons. The chip's text
(`10 ISO × 10 exp × 10 FD × 1 = 1000 frames`) wraps onto two lines and collides with the status
icons. The chip is an `AssistChip` with `onClick = {}` — it looks tappable but does nothing. Below
the preview sits ~900 px of empty white.

### 4b. Restructure `MainScreen` into preview + control bar

Replace the current "everything in one `Box` with `align()`" arrangement:

```kotlin
Column(modifier = Modifier.fillMaxSize()) {

    // 1. Preview — letterboxed on black, from step 2d.
    Box(Modifier.fillMaxWidth().background(Color.Black), contentAlignment = Alignment.Center) {
        CameraPreviewSurface(...)
        // Overlays that belong ON the preview go here:
        //  - the MANUAL_SENSOR warning banner (only when caps.supportsManualSensor is false)
        //  - the capture-progress scrim
    }

    // 2. Control bar — themed surface, fills the remaining space.
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .weight(1f)
            .windowInsetsPadding(WindowInsets.safeDrawing)
            .padding(horizontal = 24.dp, vertical = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.SpaceBetween,
    ) {
        SweepSummary(config)          // compact, two short lines
        CaptureButton(onClick = viewModel::startSweep)
        OutlinedButton(onClick = viewModel::openConfigure) { Text("Configure") }
    }
}
```

### 4c. Apply window insets — this is what stops the status-bar collision

`MainScreen` currently applies none, which is why content sits under the clock. Add
`.windowInsetsPadding(WindowInsets.safeDrawing)` to the control bar (as above). The preview stays
edge-to-edge under the status bar on purpose — that is the camera-app convention — but nothing
*interactive* may sit there any more.

Imports: `androidx.compose.foundation.layout.WindowInsets`,
`androidx.compose.foundation.layout.safeDrawing`,
`androidx.compose.foundation.layout.windowInsetsPadding`.

### 4d. Replace the chip with a quiet two-line summary

Drop the `AssistChip` entirely (it was a fake button). In its place, plain text in the control bar:

```kotlin
@Composable
private fun SweepSummary(config: SweepConfig) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            "${config.totalFrames} frames",
            style = MaterialTheme.typography.titleMedium,
        )
        Text(
            "${config.isoValues.size} ISO × ${config.exposureValuesNs.size} exposures × " +
                "${config.focusValues.size} focus" +
                if (config.framesToAverage > 1) " · avg ${config.framesToAverage}" else "",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
```

The headline number is what the operator actually checks before committing; the factor breakdown is
secondary, so it goes in the smaller, dimmer style.

### 4e. Tidy the overlays

- **Warning banner** (no `MANUAL_SENSOR`): keep, but move it inside the preview box, top-aligned,
  and give it `windowInsetsPadding(WindowInsets.statusBars)` so it clears the clock.
- **Capturing overlay**: keep the layout, swap `Color.Black.copy(alpha=0.72f)` for
  `MaterialTheme.colorScheme.scrim.copy(alpha = 0.72f)`, and keep its text white (it sits on a scrim).
- **Finished overlay**: same treatment.
- **`Initializing`**: "Opening camera…" currently renders white-on-white in light mode. Center it in
  the preview box on the black letterbox background and keep it white.

---

## 5. Regression checks — the sweep must still work

The UI rework touches the surface lifecycle, which is what feeds `CameraController`. Re-verify the
capture path end to end, not just the visuals:

1. Grant permissions, confirm live preview.
2. Configure → set ISO count 3 and exposure count 3 → Apply → summary reads `90 frames`.
3. Capture → progress overlay counts to 90 → "Sweep complete".
4. Confirm output:
   ```
   adb shell ls /sdcard/FramesSweep/ | tail -1
   adb shell ls /sdcard/FramesSweep/<newest>/ | wc -l     # expect 92 = 90 jpg + manifest + csv
   ```
5. Confirm `manifest.json` still reports `"unsettled": 0` (regression guard for the settle-tolerance
   fix) and now also carries `sensorOrientation`.

Also re-run the unit tests, which cover the sweep maths and are unaffected but cheap:

```
./gradlew :app:testDebugUnitTest
```

---

## 6. Suggested commit order

1. Theme: `themes.xml` + `MainActivity` Surface + purge hardcoded colors. *(App turns dark; white void
   becomes themed.)*
2. Aspect: `sensorOrientation` in caps + `previewDisplayAspect` + `MainScreen` uses it + letterbox.
   *(SurfaceView becomes 1080×1440; circles look circular.)*
3. Capture button restyle. *(Button visible in both themes.)*
4. Layout restructure + insets + summary text. *(No status-bar collision, no dead space.)*
5. Manifest gains `sensorOrientation`; re-run the 90-frame regression sweep.

Verify on the physical device after each step — the emulator will not reproduce the sensor-orientation
geometry.

---

## 7. Out of scope (deliberately)

- The RAW/DNG linear-averaging path (§15 of `IMPLEMENTATION_PLAN.md`) is still unimplemented; it is
  unrelated to these UI defects.
- Landscape support. The activity stays portrait-locked; the aspect helper documents what to change
  if that is ever revisited.
