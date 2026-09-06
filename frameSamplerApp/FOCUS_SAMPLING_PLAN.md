# Focus Sampling Plan — List / Uniform with distance bands

Reworks the Focus configuration section so it mirrors the ISO and shutter editors: a
**List | Uniform** toggle, where Uniform builds the focus set from four distance bands, each
given its own count, sampled as a geometric progression in distance.

This replaces the current Near/Both/Far "emphasis" model (`FocusEmphasis`, `focusValuesFor`),
which is removed.

---

## 1. What the user gets

**List mode** — unchanged from today: a comma-separated field of diopter values.

**Uniform mode** — four counts, one per distance band, plus four presets:

| Band | Distance | Diopters | Series |
|---|---|---|---|
| Infinity | 100 m – ∞ | 0.01 – 0 D | linear in diopters |
| Far | 10 – 100 m | 0.1 – 0.01 D | geometric |
| Mid | 1 – 10 m | 1 – 0.1 D | geometric |
| Near | 0.1 – 1 m | 10 – 1 D | geometric |

The five boundaries — 0.1 m, 1 m, 10 m, 100 m, ∞ — are **always present** regardless of counts.

### Why geometric-in-distance is implemented in diopter space

A geometric progression in distance is *also* a geometric progression in diopters, because
D = 1/d and the reciprocal of a geometric series is a geometric series with the reciprocal
ratio. Sampling `1 → 10 m` at ratio 10^(1/n) is identical to sampling `1 → 0.1 D` at ratio
10^(-1/n). So the generator works entirely in diopters — the unit the camera actually takes and
the unit the manifest already records — and no distance round-trip is needed.

Each of the three inner bands spans exactly one decade, so the per-step ratio is a clean
`10^(1/n)`.

### Why the infinity band is linear

The last band ends at infinity, which in distance has no finite geometric endpoint. In diopters
it is simply `[0, 0.01]`, a bounded interval, so it is sampled **linearly in diopters**. This is
the "different suitable series" the band needs, and it is the correct one: past ~100 m distance
is no longer a meaningful coordinate — every scene point out there sits within a fraction of a
pixel of the same lens position.

---

## 2. Counting convention

The five boundaries — 0.1 m, 1 m, 10 m, 100 m and ∞ — are always captured and are **not counted**.
A band's count is how many values it adds *between* its own two boundaries.

```
total values = 5 boundaries + infinity + far + mid + near
```

Consequences, all of which are the desired behaviour:
- `count = 0` on a band means "just the edges"; all four at 0 gives the minimal 5-value set.
- A band asked for `n` divides its decade into `n + 1` equal ratio steps, so the geometric ratio
  is `10^(1/(n+1))`.
- Every boundary appears exactly once — no duplicates at the joins.
- Raising one band's count never moves any value in another band.

The UI labels each row "*n* added between the edges", because that is literally what the number
does.

## 3. Device reality check — per-band capacity

The lens actuator is quantised. Measured on this device over two identical 36-point probe runs
(byte-identical results, repeat error 0.000000 D):

```
gridStep(D) ≈ 0.004114 − 0.00004326 · D    (diopters per actuator step)
```

Dividing each band's diopter span by its local grid step gives how many *physically distinct*
lens positions the band can hold:

| Band | Span | Blur span (8.53 px/D) | Distinct positions |
|---|---|---|---|
| Infinity (100 m – ∞) | 0.010 D | 0.09 px | **2** (1 interior) |
| Far (10 – 100 m) | 0.090 D | 0.77 px | 22 |
| Mid (1 – 10 m) | 0.900 D | 7.67 px | 220 |
| Near (0.1 – 1 m) | 9.000 D | 76.7 px | 2322 |

**The infinity band holds at most 2 values, i.e. its two edges plus at most 1 between them.**
Asking for more produces requests that land on the same lens position — duplicate frames that
cost capture time and storage while adding nothing.

**Design response:** each band's stepper is capped at its *interior* capacity — one less than the
table above, since the two boundaries already occupy one position between them — with the reason
shown as a hint. Nothing is silently dropped; the user simply cannot ask for a count the lens
cannot deliver.

Implement the grid law as a named constant with a comment stating it was measured on this
device. It is used **only** to cap and to warn — never to alter generated values — so on an
unmeasured device a wrong law costs at most a suboptimal cap, never wrong data.

---

## 4. Data model — `model/SweepConfig.kt`

Delete `LinearListAxis` (line 54; focus is its only user) and replace it with a focus axis that
carries its own generator state, reusing the existing `AxisMode` so the List/Uniform toggle
matches ISO and shutter.

```kotlin
/** Counts per distance band, high-diopter (nearest) band last. */
data class FocusBandCounts(
    val infinity: Int = DEFAULT_BAND_COUNT,  // 100 m .. infinity
    val far: Int = DEFAULT_BAND_COUNT,       // 10 .. 100 m
    val mid: Int = DEFAULT_BAND_COUNT,       // 1 .. 10 m
    val near: Int = DEFAULT_BAND_COUNT,      // closest .. 1 m
) {
    fun asList(): List<Int> = listOf(infinity, far, mid, near)
}

data class FocusAxis(
    val mode: AxisMode = AxisMode.RANGE,     // RANGE == "Uniform"
    val list: List<Double> = emptyList(),
    val bands: FocusBandCounts = FocusBandCounts(),
    /** The lens's closest focus, in diopters. Stored so values() is self-contained. */
    val maxDiopters: Double = 10.0,
) {
    fun values(): List<Double> = when (mode) {
        AxisMode.LIST -> list.sorted().distinct()
        AxisMode.RANGE -> bandedFocusValues(bands, maxDiopters)
    }
}
```

`maxDiopters` lives inside the axis so `SweepConfig.focusValues` keeps working with no access to
`CameraCapabilities`. `ConfigStore` already fingerprints the camera including
`minFocusDistanceDiopters`, so a stored axis can never be restored against a lens it does not
match.

`SweepConfig.focus` changes type from `LinearListAxis` to `FocusAxis`. Line 70
(`focusValues`) is unchanged.

### Band table and generator — `model/SweepDefaults.kt`

Remove `FocusEmphasis`, `focusValuesFor`, `linSpace`, `defaultFocusValues` and
`defaultFocusValuesForDiopters`. Add:

```kotlin
const val DEFAULT_BAND_COUNT = 3

/** Fixed diopter boundaries: infinity, 100 m, 10 m, 1 m, and the lens's closest focus. */
private val BAND_EDGES = doubleArrayOf(0.0, 0.01, 0.1, 1.0)

/** Actuator quantisation, measured on this device; used only to cap counts and warn. */
fun focusGridStep(diopters: Double): Double =
    (0.004114 - 0.00004326 * diopters).coerceAtLeast(1e-4)

data class FocusBand(
    val label: String,       // "1 – 10 m"
    val lowDiopters: Double,
    val highDiopters: Double,
    val geometric: Boolean,  // false only for the infinity band
) {
    /** Distinct lens positions this band can actually hold. */
    val capacity: Int
        get() = ((highDiopters - lowDiopters) / focusGridStep((lowDiopters + highDiopters) / 2))
            .toInt().coerceAtLeast(1)
}

/**
 * The bands available on a lens whose closest focus is [maxDiopters].
 *
 * Bands entirely beyond the lens's reach are dropped and the last surviving band is truncated
 * to [maxDiopters], so a lens that only focuses to 15 cm (6.67 D) gets a near band labelled
 * "0.15 – 1 m", and one that cannot focus closer than 2 m gets no near band at all.
 */
fun focusBandsFor(maxDiopters: Double): List<FocusBand>
```

`focusBandsFor` walks `BAND_EDGES` plus `maxDiopters`, keeps every interval whose low edge is
below `maxDiopters`, clamps the final high edge to `maxDiopters`, and labels each from its
distances (`1/D`, with the 0 edge rendered "∞"). The first band (low edge 0) has
`geometric = false`; all others `geometric = true`.

```kotlin
/**
 * Focus values from the per-band counts.
 *
 * Each band contributes [count] values from its low diopter edge inclusive to its high edge
 * exclusive — geometrically spaced (which is geometric in distance too, since D = 1/d), except
 * the infinity band which is linear because distance is unbounded there. One closing value at
 * [maxDiopters] completes the list, so all band boundaries are always present exactly once.
 *
 * Counts are coerced to each band's capacity so the list never contains two values the lens
 * would round to the same actuator position.
 */
fun bandedFocusValues(bands: FocusBandCounts, maxDiopters: Double): List<Double> {
    if (maxDiopters <= 0.0) return listOf(0.0)
    val table = focusBandsFor(maxDiopters)
    val out = mutableListOf<Double>()
    table.forEachIndexed { i, band ->
        out += band.lowDiopters
        val n = bands.asList()[i].coerceIn(0, band.interiorCapacity)
        if (n == 0) return@forEachIndexed
        if (band.geometric) {
            val ratio = (band.highDiopters / band.lowDiopters).pow(1.0 / (n + 1))
            for (k in 1..n) out += band.lowDiopters * ratio.pow(k.toDouble())
        } else {
            val step = (band.highDiopters - band.lowDiopters) / (n + 1)
            for (k in 1..n) out += band.lowDiopters + step * k
        }
    }
    out += maxDiopters
    return out.sorted().distinct()
}
```

Note `bands.asList()[i]` is safe because `focusBandsFor` never returns more than four bands and
drops from the near end, which is the tail of both lists — index alignment holds. Guard it with
`getOrElse(i) { DEFAULT_BAND_COUNT }` anyway.

`SweepDefaults.forCamera` builds `FocusAxis(mode = RANGE, maxDiopters = caps.minFocusDistanceDiopters.toDouble())`,
i.e. equal counts on every band, which is the requested default.

### Presets

```kotlin
data class FocusPreset(val label: String, val counts: FocusBandCounts)

fun focusPresets(): List<FocusPreset> = listOf(
    FocusPreset("Equal", FocusBandCounts(3, 3, 3, 3)),
    FocusPreset("Near",  FocusBandCounts(0, 1, 3, 9)),
    FocusPreset("Mid",   FocusBandCounts(0, 2, 9, 2)),
    FocusPreset("Far",   FocusBandCounts(1, 7, 3, 1)),
)
```

Resulting sets on this lens (counts shown after capacity clamping):

| Preset | inf / far / mid / near | Values | Range covered |
|---|---|---|---|
| Equal (default) | 1 / 3 / 3 / 3 | 15 | ∞ → 0.10 m |
| Near | 0 / 1 / 3 / 9 | 18 | dense 0.10 – 1 m |
| Mid | 0 / 2 / 9 / 2 | 18 | dense 1 – 10 m |
| Far | 1 / 7 / 3 / 1 | 17 | dense 10 – 100 m |

Example — the Mid preset, in metres:
`∞, 100, 46.4, 21.5, 10, 7.94, 6.31, 5.01, 3.98, 3.16, 2.51, 2.00, 1.58, 1.26, 1.00, 0.46, 0.22, 0.10`

Presets are ordered Equal, Near, Mid, Far so the default reads first; the three named presets
are the ones the request asks for.

---

## 5. Persistence — `storage/ConfigStore.kt`

Bump `SCHEMA_VERSION` to **2** (line 21).

Write (replacing line 43):
```kotlin
put("focus", JSONObject().apply {
    put("mode", config.focus.mode.name)
    put("list", JSONArray(config.focus.list))
    put("bands", JSONArray(config.focus.bands.asList()))
    put("maxDiopters", config.focus.maxDiopters)
})
```

Read (replacing line 70): parse the object, defaulting `mode` to `RANGE`, band counts to
`DEFAULT_BAND_COUNT` on any parse failure, and **always overwrite `maxDiopters` from the live
`caps`** rather than trusting the stored number — the fingerprint already guarantees they agree,
and taking it from caps means a stored config can never carry a stale lens limit.

**Migration from v1.** A v1 payload stores `focus` as a bare `JSONArray` of diopters. Rather than
discarding a config the user has tuned, accept version 1 and map that array into
`FocusAxis(mode = AxisMode.LIST, list = <array>, maxDiopters = caps…)`. This preserves their
explicit values, which is exactly what List mode means. Implement as:

```kotlin
val version = json.optInt("version")
if (version != SCHEMA_VERSION && version != 1) return null
```
then branch on `json.opt("focus") is JSONArray` for the v1 shape. Everything else in v1 is
unchanged between versions, so no other field needs migrating.

---

## 6. UI — `ui/AxisEditor.kt`

Rewrite `FocusAxisEditor` (lines 260–336). New signature:

```kotlin
@Composable
fun FocusAxisEditor(
    caps: CameraCapabilities,
    focus: FocusAxis,
    accentColor: Color,
    resetKey: Any,
    onFocusChange: (FocusAxis) -> Unit,
    onPreset: (FocusAxis) -> Unit,
)
```

Layout, top to bottom:

1. **Fixed-focus guard** — unchanged early return when `caps.minFocusDistanceDiopters <= 0f`.

2. **List | Uniform toggle** — `SingleChoiceSegmentedButtonRow` with two
   `SegmentedButton(icon = {})` entries, identical in construction to `GeometricAxisEditor`'s.
   Switching mode calls `onFocusChange(focus.copy(mode = …))`; it does **not** bump `resetKey`,
   so a user toggling to Uniform and back finds their typed list intact.

3. **List mode** — the existing `OutlinedTextField`, unchanged, including its
   `TextFieldValue` + `remember(resetKey)` buffer. **This buffer must be preserved verbatim.**
   The plain `(String, (String) -> Unit)` overload discards the IME's cursor position on every
   recomposition and makes the caret jump while typing; this has been fixed once already and
   must not regress.

4. **Uniform mode**
   - `FocusPresetRow` — four accent-outlined chips, built like the existing `PresetRow` but
     emitting `FocusBandCounts` instead of `GeometricAxis`. (Do not widen `PresetRow`/`AxisPreset`
     to a generic type; a parallel composable is smaller and keeps the ISO/shutter path untouched.)
   - One row per band from `focusBandsFor(maxD)`, each: band label on the left, existing
     `Stepper` on the right with `range = 1..band.capacity`, and the derived count.
     **Use `Stepper`, not `Slider`** — four sliders is too much sheet height, and the sheet is
     bottom-anchored, so any text below a slider that changes line count shifts the slider under
     the user's finger (the bug already fixed in `DownscaleEditor`; see its comment at line 267).
     Steppers do not have that failure mode.
   - A capacity hint on any band whose capacity is below `DEFAULT_BAND_COUNT`, e.g.
     *"100 m – ∞ spans 0.01 D — the lens has only 2 distinct positions there."*

5. **`ValueChips`** of the resolved values via the existing `diopterLabel`, which already renders
   `"0 D (∞)"` and `"%.2f D (%.2f m)"`. Consider extending `diopterLabel` so values below 0.01 D
   read in metres without excessive precision — a 0.0025 D chip currently renders "400.00 m",
   which is fine, but "0.00 D (400.00 m)" loses the diopter. Widen to 3 decimals for D < 0.01.

6. **Hint** replacing the emphasis text: state that each band is geometric in distance (so equal
   counts give equal *ratio* steps, not equal metre steps), that the infinity band is linear in
   diopters, and that the boundaries are always included.

### `ui/ConfigureSheet.kt`

- Line 141–146: pass `onPreset = { onConfigChange(config.copy(focus = it)); resetVersion++ }`
  alongside the existing `onFocusChange`. A preset is a programmatic replacement, so it must
  bump `resetVersion` to refresh the List field's text buffer; a normal edit must not.
- Line 261 (`resetSection`) needs no change — `defaults.focus` is now the equal-count
  `FocusAxis`, which is the correct reset target.
- Line 272 (`sectionImpact`) unchanged.

### `ui/ConfigSummaries.kt`

`focusSummary` (line 29) already renders first → last and needs no change. Optionally append the
count of bands in use; not required.

---

## 7. Tests — `app/src/test/.../SweepConfigTest.kt`

Existing tests to **delete**: the five `focusEmphasis_*` tests (lines 84–118) and
`fixedFocusLens_producesSingleFocusValue` (line 46), which call removed functions.
`defaultLikeConfig` (line 25) must be updated to build a `FocusAxis`.

New tests, all pure Kotlin — **no `android.util.Range` or any Android type**, which stubs out to
a throwing shim under unit test and has broken this suite before:

1. `bandedFocus_totalIsSumOfCountsPlusOne` — 2/3/4/5 on a 10 D lens → 15 values.
2. `bandedFocus_alwaysContainsEveryBoundary` — 0.0, 0.01, 0.1, 1.0, 10.0 all present for
   several count combinations including all-ones.
3. `bandedFocus_allOnesGivesTheFiveBoundaries` — exactly `[0.0, 0.01, 0.1, 1.0, 10.0]`.
4. `bandedFocus_isAscendingAndDistinct`.
5. `bandedFocus_midBandRatioIsTenToTheOneOverN` — with mid = 5, consecutive ratios inside
   1 – 10 m are all 10^(1/5) within 1e-9.
6. `bandedFocus_geometricInDiopterIsGeometricInDistance` — reciprocals of the mid band form a
   constant-ratio series too. This is the property the whole design rests on; assert it.
7. `bandedFocus_infinityBandIsLinearInDiopters` — differences inside 0 – 0.01 D are constant.
8. `bandedFocus_countsAreClampedToBandCapacity` — infinity = 20 yields no pair closer than
   `focusGridStep`.
9. `bandedFocus_shortLensDropsAndTruncatesBands` — `maxDiopters = 0.5` (2 m closest) gives no
   near band and ends exactly at 0.5.
10. `bandedFocus_macroLensExtendsTheNearBand` — `maxDiopters = 20.0` still ends at 20.0 with the
    near band spanning 1 – 20 D.
11. `bandedFocus_fixedFocusLensYieldsInfinityOnly` — `maxDiopters = 0.0` → `[0.0]`.
12. `focusPresets_allProduceUsableSets` — every preset yields ≥ 5 ascending distinct values.
13. `focusAxis_listModeRoundTrips` — LIST mode sorts and dedupes and ignores `bands`.

Run with `./gradlew :app:testDebugUnitTest`.

---

## 8. Files touched

| File | Change |
|---|---|
| `model/SweepConfig.kt` | drop `LinearListAxis`; add `FocusBandCounts`, `FocusAxis`; retype `SweepConfig.focus` |
| `model/SweepDefaults.kt` | drop `FocusEmphasis`/`focusValuesFor`/`linSpace`/`defaultFocusValues*`; add `FocusBand`, `focusBandsFor`, `focusGridStep`, `bandedFocusValues`, `focusPresets`; rework `forCamera` |
| `storage/ConfigStore.kt` | schema → 2, object-shaped focus, v1 migration to LIST mode |
| `ui/AxisEditor.kt` | rewrite `FocusAxisEditor`; add `FocusPresetRow`; widen `diopterLabel` precision |
| `ui/ConfigureSheet.kt` | add `onPreset` to the FOCUS branch |
| `app/src/test/.../SweepConfigTest.kt` | remove 6 tests, add 13 |

`camera/CameraController.kt`, `storage/SweepStorage.kt` and `ui/MainViewModel.kt` are
**unchanged** — they consume `config.focusValues`, whose type and meaning are identical.

---

## 9. Verification

1. `./gradlew :app:testDebugUnitTest` — all tests green.
2. `./gradlew :app:assembleDebug installDebug`.
3. Open Focus → confirm **List | Uniform** toggle, Uniform selected by default, four band rows,
   15 chips from ∞ to 0.10 m.
4. Tap each preset; confirm chips and the main-screen Focus tile count update immediately
   (no Apply button — changes apply live, per the existing behaviour).
5. Confirm the infinity band's stepper stops at 2 and shows its capacity hint.
6. Switch to List, type values, rotate the device and background/foreground the app — the list
   and the caret behaviour must survive both.
7. Force-stop and relaunch — the Uniform band counts persist.
8. Run a short sweep (all bands at 1, ISO 1, shutter 1 → 5 frames) and check `manifest.json`
   `focusValuesDiopters` holds the five boundaries and every `actualFocusDiopters` is distinct.
9. Delete the verification session **by its exact name** from a directory listing taken before
   the run.

---

## 10. Notes carried forward

- **This is scene-based, not blur-based, sampling.** Defocus blur is linear in diopters at
  8.53 px/D on this rig, so the four bands hold 76.7 / 7.67 / 0.77 / 0.09 px of blur
  respectively — wildly unequal. Equal counts therefore oversample the far bands relative to
  what an optical analysis would want. That is a deliberate choice: it produces a mesh whose
  *subjects* are evenly distributed across scene depth, which is what a distance-varied image
  set needs. Nothing in this plan should try to "correct" it back toward diopter-uniform.
- The CoC-limited alternative (86 values at 0.1173 D, gap-free in blur) is still reachable
  through List mode, and remains the right choice for defocus/PSF work.
- Do not downscale focus sweeps: the Airy disk is 1.21 px, so even 2× binning undersamples
  the PSF.
