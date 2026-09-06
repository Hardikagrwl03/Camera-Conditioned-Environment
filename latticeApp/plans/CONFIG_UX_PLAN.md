# Frame Sampler — Per-Configuration Editing Plan

Goal: the main screen lists the sweep configurations (ISO, Exposure, Focus, Options). Each entry is
tappable and opens **its own** focused overlay, instead of one monolithic "Configure sweep" sheet
that holds all four.

---

## 0. Where things stand

**Current behaviour.** The main screen shows a two-line text summary and a single `Configure`
button. That button opens `ConfigureSheet` — one full-screen dialog stacking all four cards (ISO,
Exposure time, Focus distance, Options) behind a single Cancel/Apply pair. To change one ISO value
the operator opens everything and scrolls past unrelated settings.

**Target behaviour.** Four tappable entries on the main screen, each showing its current state at a
glance, each opening only its own editor.

**Files involved:**

| File | Role today | Change |
|---|---|---|
| `ui/MainScreen.kt` | preview + summary + Configure button | replace summary/button with the config list |
| `ui/ConfigureSheet.kt` | one dialog, all four cards | becomes the per-section dialog |
| `ui/AxisEditor.kt` | `SectionCard`, `ValuePreview`, `Hint`, `GeometricAxisEditor` | editors made reusable |
| `ui/MainViewModel.kt` | `configureSheetVisible: Boolean` | becomes `editingSection: ConfigSection?` |

### 0a. The space budget — read this before designing the list

Measured on the device earlier this session (Samsung SM-S711B, 1080×2340, density 2.8125):

| Region | Pixels | dp |
|---|---|---|
| Screen height | 2340 | 832 |
| Preview (3:4, fixed by sensor aspect) | 1440 | 512 |
| **Control area remaining** | **900** | **320** |
| Minus bottom gesture-bar inset | ~50 | ~18 |
| **Usable** | **~850** | **~302** |

Everything the control area holds must fit in roughly **300 dp**, and the preview cannot be shrunk
without breaking the 3:4 correctness that was just fixed. A naive vertical list of four 56 dp rows
plus the 76 dp capture button plus a totals line **does not fit** (~380 dp). The layout below is
built around that constraint — see §4.

---

## 1. Model the selection — `ui/MainViewModel.kt`

Replace the boolean with a nullable section, so exactly one editor can be open and the screen knows
which:

```kotlin
enum class ConfigSection(val title: String) {
    ISO("ISO"),
    EXPOSURE("Exposure time"),
    FOCUS("Focus distance"),
    OPTIONS("Options"),
}
```

In `MainViewModel`:

```kotlin
// was: var configureSheetVisible by mutableStateOf(false)
var editingSection by mutableStateOf<ConfigSection?>(null)
    private set

fun openSection(section: ConfigSection) { editingSection = section }
fun closeSection() { editingSection = null }

fun applyConfig(newConfig: SweepConfig) {
    val state = _uiState.value as? UiState.Preview ?: return
    _uiState.value = state.copy(config = newConfig)
    editingSection = null
}
```

`applyConfig` keeps its existing signature — each editor hands back a whole `SweepConfig` with only
its own field replaced, so nothing downstream (the sweep, the manifest) changes.

Delete `openConfigure()` and `cancelConfigure()`; `closeSection()` replaces both.

---

## 2. One definition of each summary — new file `ui/ConfigSummaries.kt`

The tiles and the editors must never disagree about what an axis currently holds, so the strings get
written once. These are pure functions over `SweepConfig` / `CameraCapabilities`.

```kotlin
/** "50, 79, 126 … 3200" — long axes elide the middle so both endpoints stay visible. */
fun elide(values: List<String>): String = when {
    values.isEmpty() -> "—"
    values.size <= 3 -> values.joinToString(", ")
    else -> values.take(2).joinToString(", ") + " … " + values.last()
}

fun isoSummary(config: SweepConfig): String =
    elide(config.isoValues.map { it.toString() })

fun exposureSummary(config: SweepConfig): String =
    elide(config.exposureValuesNs.map { trim(it / 1e6) }) + " ms"

fun focusSummary(config: SweepConfig): String {
    val v = config.focusValues
    return when {
        v.isEmpty() -> "—"
        v.size == 1 -> diopterLabel(v.first().toDouble())
        else -> "${diopterLabel(v.first().toDouble())} → ${diopterLabel(v.last().toDouble())}"
    }
}

fun optionsSummary(config: SweepConfig): String =
    "avg ${config.framesToAverage} · settle ${config.settleFrames}"

/** Count shown on each tile; Options has no value count. */
fun sectionCount(section: ConfigSection, config: SweepConfig): String? = when (section) {
    ConfigSection.ISO -> "${config.isoValues.size}"
    ConfigSection.EXPOSURE -> "${config.exposureValuesNs.size}"
    ConfigSection.FOCUS -> "${config.focusValues.size}"
    ConfigSection.OPTIONS -> null
}
```

Move `trim()` and `diopterLabel()` here out of `ConfigureSheet.kt` (they are currently private there)
and have `ConfigureSheet` import them. `ValuePreview` in `AxisEditor.kt` should be reworked to call
`elide()` rather than keeping its own copy of that logic.

---

## 3. Make the editors reusable — `ui/AxisEditor.kt` and `ui/ConfigureSheet.kt`

`FocusAxisEditor` and `OptionsCard` are `private` inside `ConfigureSheet.kt` today. The per-section
dialog needs all three editors, so:

1. Move `FocusAxisEditor` and `OptionsCard` into `AxisEditor.kt` (which already holds
   `GeometricAxisEditor`), dropping `private`.
2. Rename `OptionsCard` → `OptionsEditor` for consistency with the other two.
3. Keep every editor's existing signature, including **`resetKey`** — the `TextFieldValue` buffers
   keyed on it are what stop the cursor jumping to the start on each keystroke. Do not simplify
   this away; it is a fixed bug, not incidental complexity.
4. Keep `SectionCard` as the wrapper. Inside the per-section dialog there is only one card, which
   is the point — the card's own title row still carries the "N values" badge.

---

## 4. The config list on the main screen — `ui/MainScreen.kt`

### 4a. Layout: a 2×2 grid, not a vertical list

Four rows do not fit in 300 dp (§0a). A 2×2 grid of compact tiles does:

```
┌─────────────────┬─────────────────┐
│ ISO          10 │ Exposure     10 │   each tile ~78 dp
│ 50, 79 … 3200   │ 0.1, 0.215 … 100│
├─────────────────┼─────────────────┤
│ Focus        10 │ Options         │
│ 0 D (∞) → 10 D  │ avg 1 · settle 2│
└─────────────────┴─────────────────┘
              ( ● )                      capture, 76 dp
      1000 frames · est. 2 min 49 s      totals, ~18 dp
```

Budget: 2 × 78 + 8 gap + 76 + 18 + spacing/padding ≈ 290 dp. Fits, with little to spare.

**Build it with two `Row`s, not `LazyVerticalGrid`.** There are exactly four fixed items; a lazy
grid nested in a column needs a height constraint and buys nothing here.

```kotlin
@Composable
private fun ConfigGrid(
    config: SweepConfig,
    onSectionClick: (ConfigSection) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ConfigTile(ConfigSection.ISO, config, onSectionClick, Modifier.weight(1f))
            ConfigTile(ConfigSection.EXPOSURE, config, onSectionClick, Modifier.weight(1f))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ConfigTile(ConfigSection.FOCUS, config, onSectionClick, Modifier.weight(1f))
            ConfigTile(ConfigSection.OPTIONS, config, onSectionClick, Modifier.weight(1f))
        }
    }
}
```

### 4b. The tile

```kotlin
@Composable
private fun ConfigTile(
    section: ConfigSection,
    config: SweepConfig,
    onClick: (ConfigSection) -> Unit,
    modifier: Modifier = Modifier,
) {
    val summary = when (section) {
        ConfigSection.ISO -> isoSummary(config)
        ConfigSection.EXPOSURE -> exposureSummary(config)
        ConfigSection.FOCUS -> focusSummary(config)
        ConfigSection.OPTIONS -> optionsSummary(config)
    }
    val count = sectionCount(section, config)

    Card(
        onClick = { onClick(section) },
        modifier = modifier
            .heightIn(min = 72.dp)
            .semantics { contentDescription = "Edit ${section.title}" },
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    section.title,
                    style = MaterialTheme.typography.labelLarge,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false),
                )
                if (count != null) {
                    Text(
                        count,
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Text(
                summary,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}
```

`Card(onClick = ...)` gives the ripple and the correct role for free. The `contentDescription`
("Edit ISO", …) is what makes each tile findable by `uiautomator dump` in §8 — pixel-guessing tap
targets wasted a lot of time earlier in this project.

### 4c. Wire it into the control column

Replace `SweepSummary` + the `Configure` `OutlinedButton` with:

```kotlin
Column(
    modifier = Modifier
        .fillMaxWidth()
        .weight(1f)
        .windowInsetsPadding(WindowInsets.safeDrawing)
        .verticalScroll(rememberScrollState())      // safety valve, see 4d
        .padding(horizontal = 16.dp, vertical = 12.dp),
    horizontalAlignment = Alignment.CenterHorizontally,
    verticalArrangement = Arrangement.spacedBy(12.dp),
) {
    ConfigGrid(config = state.config, onSectionClick = viewModel::openSection)
    CaptureButton(onClick = viewModel::startSweep)
    Text(
        "${state.config.totalFrames} frames · est. ${formatDuration(...)}",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}
```

The `Configure` button disappears entirely — the tiles are the entry points.

### 4d. Two guards against the tight budget

The 290 dp estimate assumes the default font scale. At 1.3× accessibility font scale it overflows.
Handle both, cheaply:

1. **`verticalScroll` on the control column** (above) — content scrolls rather than clipping. This
   alone prevents the worst outcome, a capture button pushed off screen.
2. **Let the preview yield if it must.** Change the preview box from wrap-height to bounded:

   ```kotlin
   Box(
       modifier = Modifier.fillMaxWidth().weight(1f, fill = false).background(Color.Black),
       contentAlignment = Alignment.Center,
   ) {
       AndroidView(
           modifier = Modifier.aspectRatio(aspect, matchHeightConstraintsFirst = true),
           ...
       )
   }
   ```

   With `matchHeightConstraintsFirst = true` the preview shrinks to whatever height is left and
   letterboxes at the sides, **keeping the 3:4 ratio intact**. The aspect correctness fixed earlier
   is preserved; only the size gives.

---

## 5. The per-section overlay — rewrite `ui/ConfigureSheet.kt`

One dialog, parameterised by section. Keep the full-screen `Dialog` with
`decorFitsSystemWindows = false` — that combination is already proven to handle the status bar and
the IME correctly here; a `ModalBottomSheet` would reopen those questions for no real gain.

```kotlin
@Composable
fun ConfigSectionSheet(
    section: ConfigSection,
    initialConfig: SweepConfig,
    caps: CameraCapabilities,
    onApply: (SweepConfig) -> Unit,
    onCancel: () -> Unit,
) {
    var draft by remember(section) { mutableStateOf(initialConfig) }
    var resetVersion by remember(section) { mutableStateOf(0) }

    Dialog(
        onDismissRequest = onCancel,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        Surface(modifier = Modifier.fillMaxSize()) {
            Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {

                Row(/* Cancel · section.title · Apply */) { ... }
                HorizontalDivider()

                Column(
                    modifier = Modifier.weight(1f).verticalScroll(rememberScrollState())
                        .padding(horizontal = 16.dp, vertical = 12.dp),
                ) {
                    when (section) {
                        ConfigSection.ISO -> GeometricAxisEditor(
                            title = "ISO",
                            axis = draft.iso,
                            unitLabel = "ISO",
                            supportedHint = "Camera supports ${caps.sensitivityRange.lower} – ${caps.sensitivityRange.upper}",
                            resetKey = resetVersion,
                            onAxisChange = { draft = draft.copy(iso = it) },
                            formatValue = { it.roundToInt().toString() },
                        )
                        ConfigSection.EXPOSURE -> GeometricAxisEditor( /* as today */ )
                        ConfigSection.FOCUS -> FocusAxisEditor(
                            caps = caps,
                            focus = draft.focus,
                            resetKey = resetVersion,
                            onFocusChange = { draft = draft.copy(focus = it) },
                            onFocusGenerated = { draft = draft.copy(focus = it); resetVersion++ },
                        )
                        ConfigSection.OPTIONS -> OptionsEditor(
                            draft = draft,
                            resetKey = resetVersion,
                            onDraftChange = { draft = it },
                            onResetDefaults = { draft = SweepDefaults.forCamera(caps); resetVersion++ },
                        )
                    }
                }

                HorizontalDivider()
                SectionFooter(section, draft)
            }
        }
    }
}
```

Notes that matter:

- **`remember(section)`** keys the draft to the section, so opening a different tile starts from the
  live config rather than a stale draft.
- **Apply is per-section but commits a whole `SweepConfig`.** Because `draft` starts as a copy of the
  current config and only that section's field is touched, applying cannot clobber another axis.
- **Cancel discards** — same as today, and the existing behaviour is already verified.
- **Disable Apply** when the edited axis resolves to zero values (`draft.totalCaptures > 0` covers
  it, since an empty axis zeroes the product).

### 5a. Footer shows the impact of this edit

Each section's footer states what the change does to the whole sweep, so the operator does not have
to back out to find out:

```kotlin
@Composable
private fun SectionFooter(section: ConfigSection, draft: SweepConfig) {
    val sectionPart = when (section) {
        ConfigSection.ISO -> "${draft.isoValues.size} ISO values"
        ConfigSection.EXPOSURE -> "${draft.exposureValuesNs.size} exposures"
        ConfigSection.FOCUS -> "${draft.focusValues.size} focus distances"
        ConfigSection.OPTIONS -> "avg ${draft.framesToAverage} · settle ${draft.settleFrames}"
    }
    Text("$sectionPart → ${draft.totalFrames} frames total · est. ${formatDuration(...)}")
}
```

### 5b. "Reset to defaults" scope

It currently lives in Options and resets **everything**. Inside a per-section dialog that is
surprising — a user in "Options" would not expect ISO to change. Relabel it **"Reset all settings"**
and keep it in Options only, so the destructive scope is stated.

---

## 6. Call site in `MainScreen`

```kotlin
val state = uiState
val section = viewModel.editingSection
if (state is UiState.Preview && section != null) {
    ConfigSectionSheet(
        section = section,
        initialConfig = state.config,
        caps = state.caps,
        onApply = viewModel::applyConfig,
        onCancel = viewModel::closeSection,
    )
}
```

---

## 7. Build order

1. `ConfigSection` enum + ViewModel `editingSection` (compiles, nothing visible yet).
2. `ConfigSummaries.kt`; point `ValuePreview` at `elide()`.
3. Move `FocusAxisEditor`/`OptionsEditor` into `AxisEditor.kt`, drop `private`.
4. `ConfigSectionSheet` replacing `ConfigureSheet`.
5. `ConfigTile` + `ConfigGrid`; delete `SweepSummary` and the `Configure` button.
6. Preview `weight(1f, fill = false)` + `matchHeightConstraintsFirst` + control-column scroll.

---

## 8. Acceptance checks

Visual and structural, on the physical device:

```
# All four tiles present and addressable
adb shell uiautomator dump /sdcard/wd.xml && adb shell cat /sdcard/wd.xml \
  | grep -o 'content-desc="Edit [^"]*"'
# expect: Edit ISO / Edit Exposure time / Edit Focus distance / Edit Options

# Preview aspect must NOT regress
adb shell cat /sdcard/wd.xml | grep -o 'class="android.view.SurfaceView"[^/]*bounds="\[[0-9,]*\]\[[0-9,]*\]"'
# expect [0,0][1080,1440]

# Capture button still on screen (not pushed off by the grid)
adb shell cat /sdcard/wd.xml | grep -o 'content-desc="Start capture sweep"[^/]*bounds="\[[0-9,]*\]\[[0-9,]*\]"'
# y-max must be < 2340
```

Behavioural:

1. Tap **ISO** → dialog titled "ISO", containing only the ISO card. Set Steps 3 → footer reads
   "3 ISO values → 90 frames total" (with focus at 10, exposure at 3). Apply → ISO tile shows
   `50, 400 … 3200` and `3`.
2. Tap **Exposure time** → only the exposure card. Cancel → tile unchanged.
3. Tap **Focus distance** → "Space evenly" with Count 3 → tile shows `0 D (∞) → 10.00 D (0.10 m)`
   and `3`.
4. Tap **Options** → avg/settle only. "Reset all settings" restores every tile to defaults.
5. Editing a field still works first time — backspace clears, typing appends (guards the
   `TextFieldValue` cursor fix).
6. Run a 3×3×10 sweep to completion: 92 files, `"unsettled": 0` in `manifest.json`.

Plus `./gradlew :app:testDebugUnitTest`.

---

## 9. Risks

- **The 300 dp budget is the real constraint.** If the grid feels cramped in practice, the fix is
  §4d's `matchHeightConstraintsFirst` preview, not shrinking touch targets below 48 dp.
- **Do not regress the cursor fix.** Every editor keeps its `resetKey` + `TextFieldValue` buffers.
  A quick way to regress it is "simplifying" an editor back to the `(String, (String) -> Unit)`
  overload while moving files in step 3.
- **Do not regress the preview aspect.** Step 6 touches the preview modifiers; §8's SurfaceView
  bounds check is the guard.
- The device was disconnected when this plan was written, so §0a's numbers come from measurements
  taken earlier in this session rather than a fresh reading. Re-run `wm size` / `wm density` before
  relying on the budget if you are on different hardware.

---

## 10. Out of scope

- Reordering or disabling axes (e.g. sweeping only ISO) — the sweep is always the full product.
- Per-section presets/saved configurations.
- The RAW/DNG path (§15 of `IMPLEMENTATION_PLAN.md`), still unimplemented.
