# Frame Sampler — UI Polish Plan

Goal: a preview screen with **six small attribute tabs** — ISO, Shutter, Focus, Format, Average,
Settle — each opening a **partial-screen popup** rather than a full-screen dialog, and the whole
thing made elegant, clean and vibrant rather than merely functional.

---

## 0. What changes

| Today | After |
|---|---|
| 4 tiles: ISO, Exposure time, Focus distance, **Options** (format + avg + settle lumped together) | **6 tabs**: ISO · Shutter · Focus · Format · Average · Settle |
| Tap opens a **full-screen** Dialog | Tap opens a **bottom sheet** covering only the lower part of the screen |
| Monochrome tiles, all identical | Each attribute has its **own accent colour**, carried from tab into its sheet |
| Every editor is text fields | Simple attributes get **steppers and choice cards** — no keyboard at all for Format/Average/Settle |
| Full-screen scrim hides the preview while capturing | Preview stays visible; progress lives in the control bar |

### 0a. Space budget (measured: 1080×2340 at 450 dpi = 384×832 dp)

| Region | dp |
|---|---|
| Screen height | 832 |
| Preview at 3:4 full width | 512 |
| Control area | 320 |
| Bottom gesture inset | ~18 |
| **Usable for tabs + capture** | **~302** |

Six tabs must fit alongside the capture button. A 3-column × 2-row grid of 56–64 dp chips costs
120–136 dp, leaving room for the 76 dp capture button, the totals line and spacing (~270–285 dp
total). It fits. The `weight(1f, fill = false)` preview and the scrollable control column added
earlier stay as pressure valves for large font scales.

---

## 1. Design system

Define these once in `ui/theme/` so nothing is hand-tuned at call sites.

### 1a. Accent palette — where the colour comes from

Material You dynamic colour already themes the app to the device wallpaper, and that stays as the
**foundation** (surfaces, capture button, buttons). Vibrancy comes from a fixed six-hue **accent
set** used only for small, high-signal elements: the tab's dot and value text, and its sheet's
header. Keeping accents off large surfaces is what stops six hues turning into noise, and stops
them clashing with an arbitrary dynamic palette.

New file `ui/theme/AccentColors.kt`:

```kotlin
/** One accent per configuration attribute, tuned for contrast on both light and dark surfaces. */
enum class Accent(private val light: Color, private val dark: Color) {
    ISO(Color(0xFFF57C00), Color(0xFFFFB74D)),        // amber
    SHUTTER(Color(0xFF00838F), Color(0xFF4DD0E1)),    // cyan
    FOCUS(Color(0xFF6A3DB8), Color(0xFFB39DDB)),      // violet
    FORMAT(Color(0xFF2E7D32), Color(0xFF81C784)),     // green
    AVERAGE(Color(0xFFC2185B), Color(0xFFF48FB1)),    // pink
    SETTLE(Color(0xFF1565C0), Color(0xFF90CAF9));     // blue

    @Composable fun color(): Color = if (isSystemInDarkTheme()) dark else light

    /** Tinted container for the chip background and sheet header, kept deliberately faint. */
    @Composable fun container(): Color = color().copy(alpha = if (isSystemInDarkTheme()) 0.16f else 0.10f)
}
```

Rules that keep it tasteful:

- Accent is used for: the tab's leading dot, the tab's **value** text, the sheet header bar, and
  the selected state inside that sheet. Nothing else.
- Labels, body text and surfaces stay Material You (`onSurface`, `onSurfaceVariant`, `surface`).
- The capture button stays `primary` — one dominant action, one dominant colour.
- Never put accent text on an accent container without checking contrast; the containers above are
  ≤16 % alpha precisely so `onSurface` text stays readable on them.

### 1b. Shape, spacing, motion

- Tabs and sheets: `RoundedCornerShape(18.dp)`; sheet top corners 28 dp.
- Spacing scale: 4 / 8 / 12 / 16 / 24 dp only.
- Value changes animate: `animateContentSize()` on the tab, and `Crossfade` on the value text, so
  applying a sheet visibly lands on the tab instead of snapping.
- Capture button press: scale to 0.92 via `animateFloatAsState`, plus
  `HapticFeedbackType.LongPress` on start.

---

## 2. The preview screen

```
┌──────────────────────────────────┐
│                                  │
│         camera preview           │  512 dp, 3:4, unchanged geometry
│   (subtle gradient scrim top)    │
│                                  │
├──────────────────────────────────┤
│  ● ISO      ● SHUTTER   ● FOCUS  │
│    10         10          10     │  3 × 2 grid of tabs, ~60 dp each
│  ● FORMAT   ● AVERAGE   ● SETTLE │
│    PNG        1           2      │
│                                  │
│              ( ● )               │  capture, 76 dp
│      1000 frames · 2 min 49 s    │  totals pill
└──────────────────────────────────┘
```

### 2a. Preview polish

- Keep the 3:4 aspect and the `matchHeightConstraintsFirst` guard exactly as they are — that
  correctness was hard-won, do not touch it.
- Add a top scrim so the status bar stays legible over a bright scene:
  `Brush.verticalGradient(listOf(Color.Black.copy(0.45f), Color.Transparent))`, 96 dp tall,
  drawn inside the preview box, top-aligned.
- Round the preview's bottom corners by 20 dp so it reads as a card rather than a hard cut.

### 2b. The tab

```kotlin
@Composable
private fun AttributeTab(
    section: ConfigSection,
    value: String,          // "10", "PNG", "1"
    accent: Accent,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val tint = accent.color()
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(18.dp),
        color = accent.container(),
        modifier = modifier
            .heightIn(min = 56.dp)
            .semantics { contentDescription = "Edit ${section.title}" },
    ) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(6.dp).clip(CircleShape).background(tint))
                Spacer(Modifier.width(6.dp))
                Text(
                    section.shortLabel.uppercase(),      // "ISO", "SHUTTER", "FOCUS", …
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                )
            }
            Crossfade(targetState = value, label = "tabValue") { v ->
                Text(
                    v,
                    style = MaterialTheme.typography.titleMedium,
                    color = tint,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}
```

The label is the *attribute*, the accent-coloured value is the *state*. That split is what lets six
tabs be scanned at a glance.

### 2c. Totals

Replace the plain text line with a soft pill:

```kotlin
Surface(shape = RoundedCornerShape(50), color = MaterialTheme.colorScheme.surfaceVariant) {
    Text("1000 frames · 2 min 49 s", Modifier.padding(horizontal = 14.dp, vertical = 6.dp), …)
}
```

Turn it `errorContainer` when `totalCaptures == 0` or the estimated size exceeds free space — the
one place a warning belongs on this screen.

---

## 3. Model changes — six sections

`ConfigSection` currently has four entries; split `OPTIONS` into three and add display metadata:

```kotlin
enum class ConfigSection(
    val title: String,        // sheet header: "Shutter speed"
    val shortLabel: String,   // tab label: "SHUTTER"
    val accent: Accent,
) {
    ISO("ISO sensitivity", "ISO", Accent.ISO),
    SHUTTER("Shutter speed", "Shutter", Accent.SHUTTER),
    FOCUS("Focus distance", "Focus", Accent.FOCUS),
    FORMAT("Output format", "Format", Accent.FORMAT),
    AVERAGE("Frames to average", "Average", Accent.AVERAGE),
    SETTLE("Settle frames", "Settle", Accent.SETTLE),
}
```

**Naming note:** you called it SS/shutter speed; the UI adopts "Shutter" throughout. The data model
and `manifest.json` keep `exposure` / `exposureValuesNs` unchanged, so existing analysis scripts and
the sessions already on disk stay readable. Flagging the deliberate split between UI wording and
data wording.

`sectionSummary`/`sectionCount` in `ConfigSummaries.kt` extend to the six cases, returning the short
value strings the tabs show: `"10"`, `"PNG"`, `"1"`, `"2"`.

---

## 4. The partial popup

`ModalBottomSheet` — it is the standard component for "covers only part of the screen", gives
drag-to-dismiss and a scrim for free, and leaves the preview visible above.

```kotlin
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConfigSheet(section: ConfigSection, …) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(
        onDismissRequest = onCancel,
        sheetState = sheetState,
        shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp),
        dragHandle = { BottomSheetDefaults.DragHandle() },
        // The sheet must not consume IME insets itself; the content handles them (see 4b).
        contentWindowInsets = { WindowInsets(0) },
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .imePadding()
                .padding(horizontal = 20.dp)
                .padding(bottom = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            SheetHeader(section)          // 4a
            SheetBody(section, draft, …)  // §5
            SheetFooter(section, draft, onApply, onCancel)  // 4c
        }
    }
}
```

### 4a. Header — accent, title, purpose

```
 ●  Shutter speed                         10 values
    How long each frame is exposed.
```

Accent dot + title in `titleLarge`, resolved count on the right in the accent colour, and a single
`bodySmall` line saying what the attribute does. That description line is the "detailed" part — it
is the only place in the app that explains each control, and it costs one line.

### 4b. Keyboard behaviour — the main risk, handle it explicitly

Three of six sheets need no keyboard at all (§5d–f). For the three that do, the sheet must rise
above the IME: `contentWindowInsets = { WindowInsets(0) }` on the sheet **plus** `.imePadding()` on
the content column. Getting exactly one of those wrong is what produces a sheet hidden behind the
keyboard, so it is an explicit acceptance check in §8.

### 4c. Footer — impact and commit

```
 3 values → 90 frames · est. 15 s          [ Cancel ]  [ Apply ]
```

Live impact on the left, actions on the right. `Apply` disabled when the section resolves to zero
values. Keep the existing draft semantics exactly: draft starts as a copy of the live config, only
this section's field is replaced, so applying one section cannot clobber another.

---

## 5. The six sheets

### 5a. ISO — accent amber

- Segmented control: **List** | **Geometric**.
- Geometric: `From` / `To` / `Steps` in a row (existing `GeometricAxisEditor` inputs).
- List: comma-separated field, `maxLines = 2`.
- **Quick presets** as accent-outlined chips: `Full range (10)` · `Low 50–400` · `Native 50` ·
  `Analog max 640`. That last one is genuinely useful here — the device's
  `maxAnalogSensitivity` is 640, above which gain is digital.
- Resolved values as a wrapping `FlowRow` of small chips — there is room in a dedicated sheet, so
  show them all rather than eliding.
- Hint: `Camera supports 50 – 3200`.

### 5b. Shutter — accent cyan

Same structure as ISO, in milliseconds, plus:

- Presets: `Full range (10)` · `Fast 0.1–10 ms` · `Slow 10–100 ms`.
- Each resolved chip also shows the reciprocal, which is how shutter speed is normally read:
  `3.162 ms (1/316 s)`.
- Hint: `Camera supports 0.085 – 100 ms`.

### 5c. Focus — accent violet

- Comma-separated diopters field, plus `Count` + **Space evenly**.
- A **slider** for count (1–20) instead of a text field — it is a small bounded integer, so a
  slider is faster and needs no keyboard.
- Presets: `Infinity only` · `Near + far` · `10 evenly`.
- Resolved chips show both units: `2.22 D (0.45 m)`.
- Hint: `0 D = infinity · 10.00 D = closest focus`.

### 5d. Format — accent green, **no keyboard**

Two full-width selectable cards, the selected one outlined in the accent:

```
┌──────────────────────────────────────────┐
│ ◉ JPEG                        ~5 MB/frame│
│   Compressed. Written straight from the  │
│   camera encoder. Lossy: 4:2:0 chroma    │
│   subsampling and DCT quantization.      │
├──────────────────────────────────────────┤
│ ○ PNG                        ~30 MB/frame│
│   Lossless encode, captured uncompressed │
│   so it carries no JPEG artifacts. Still │
│   processed 8-bit output, not sensor RAW.│
└──────────────────────────────────────────┘
```

Below them, a live storage line: `8 frames ≈ 240 MB · 3.3 GB free`, turning `error` coloured when
the estimate exceeds free space. This is where the format's real cost becomes visible before the
sweep rather than at the point of failure.

### 5e. Average — accent pink, **no keyboard**

- A **stepper**: `[ − ]  4  [ + ]`, range 1–64, value in `displayMedium` accent colour.
- Below: `Averages 4 frames per configuration to reduce noise.`
- The honest caveat, shown only when > 1 **and** format is JPEG, because that is exactly when it
  bites: *"With JPEG this re-encodes the averaged frame, adding a second generation of compression
  loss. PNG avoids it."* — with an inline **Switch to PNG** action.
- Impact line: `× 4 frames = 360 total` — the multiplier on sweep length is the thing users miss.

### 5f. Settle — accent blue, **no keyboard**

- Stepper 0–10, default 2.
- Explanation: `Warm-up frames discarded after each settings change, so the sensor has applied the
  new ISO, shutter and focus before the frame that gets kept.`
- Warning at 0: *"With 0 the first frame after each change may still carry the previous settings."*

---

## 6. Capture state (worth doing while here)

The current full-screen scrim hides the preview during a sweep. On a camera rig, watching the
frames go by is useful. Replace it with:

- Preview stays live and unscrimmed.
- The tab grid is replaced in place by a **capture panel**: a linear progress bar in `primary`,
  `Capturing 37 / 90`, the current `ISO 800 · 15.0 ms · 2.50 D` in accent colours matching their
  tabs, and a `Cancel` text button.
- The capture button becomes a **progress ring** filling as the sweep proceeds, with a square stop
  glyph.

Finished state stays a small centred card, not a full-screen scrim.

---

## 7. Implementation order

1. `AccentColors.kt`; extend `ConfigSection` to six entries with `shortLabel` + `accent`; extend
   `ConfigSummaries` to six cases. *(Compiles; nothing visible.)*
2. `AttributeTab` + 3×2 grid replacing `ConfigGrid`/`ConfigTile`. Preview scrim + rounded corners +
   totals pill. *(Six tabs on screen, still opening the old full-screen dialog.)*
3. Swap the Dialog for `ModalBottomSheet` with header/body/footer, routing the three existing
   editors. *(Partial popups working.)*
4. New sheets: Format cards, Average stepper, Settle stepper. *(Options split done; the old
   `OptionsEditor` is deleted.)*
5. Presets, FlowRow value chips, reciprocal/metre annotations.
6. Capture-state panel and progress ring (§6).
7. Motion: crossfade, `animateContentSize`, press scale, haptics.

Each step is independently shippable and independently verifiable on the device.

---

## 8. Acceptance checks

```
# Six tabs, all addressable
adb shell uiautomator dump /sdcard/wd.xml && adb shell cat /sdcard/wd.xml \
  | grep -o 'content-desc="Edit [^"]*"'
# expect exactly 6: ISO sensitivity, Shutter speed, Focus distance,
#                   Output format, Frames to average, Settle frames

# Preview geometry must NOT regress
adb shell cat /sdcard/wd.xml | grep -o 'class="android.view.SurfaceView"[^/]*bounds="\[[0-9,]*\]\[[0-9,]*\]"'
# width/height ratio must be 0.750 ± 0.005

# Capture button still on screen
adb shell cat /sdcard/wd.xml | grep -o 'content-desc="Start capture sweep"[^/]*bounds="[^"]*"'
# y-max < 2340
```

Behavioural, per sheet:

1. Each tab opens a sheet **covering part of the screen** — assert the sheet's top bound is
   > 0 and the preview's SurfaceView is still in the dump (proving it was not replaced full-screen).
2. **Keyboard test (§4b)**: open ISO, tap `From`; the sheet's input must remain visible above the
   IME. This is the highest-risk item.
3. Format sheet: select PNG → tab reads `PNG`, storage line updates, `manifest.json` of a
   subsequent sweep records `"outputFormat": "PNG"`.
4. Average stepper: `+` to 4 → tab reads `4`, footer shows `× 4`, JPEG caveat appears; switching to
   PNG makes it disappear.
5. Settle stepper: `−` to 0 → warning appears.
6. Applying one sheet leaves the other five tabs unchanged.
7. Both themes: `adb shell cmd uimode night no|yes` — accents legible on both, then restore.
8. Regression: a 2×2×2 sweep completes; `unsettled: 0`; file count = captures + 2.
9. `./gradlew :app:testDebugUnitTest`.

---

## 9. Risks

- **`ModalBottomSheet` + IME** is the real risk and the reason the earlier plan chose a
  full-screen Dialog. Mitigation: three sheets avoid the keyboard entirely; the other three get the
  explicit inset recipe in §4b and the §8.2 check. If it proves unstable, the fallback is a
  centred `Dialog` with `usePlatformDefaultWidth = false` wrapping a `Card` at ~92 % width — still
  a partial overlay, still elegant, without the sheet's IME machinery.
- **`ExperimentalMaterial3Api`** — `ModalBottomSheet` requires the opt-in; acceptable, but it means
  the API can shift under a Compose BOM bump.
- **Six accents can look like a toybox** if they leak onto large surfaces. The discipline in §1a
  (accents only on dot, value, header, selection) is what keeps it vibrant rather than garish. If
  it still reads as busy, the first thing to cut is the tab container tint, leaving only the dot
  and value coloured.
- **Space.** Six tabs plus the capture button leaves ~20 dp of slack at default font scale. The
  existing scroll + `matchHeightConstraintsFirst` guards absorb larger scales; do not remove them.
- **Do not regress** the `TextFieldValue`/`resetKey` cursor handling when editors move into sheets.

---

## 10. Out of scope

- RAW/DNG capture (answered separately: the device supports it, `whiteLevel` 1023, GBRG Bayer —
  it would add a third format card in §5d when built).
- Landscape layout; the activity stays portrait-locked.
- Reordering or disabling sweep axes.
