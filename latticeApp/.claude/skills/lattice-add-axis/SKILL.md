---
name: lattice-add-axis
description: Add a new swept parameter (a sweep axis) or a new per-frame setting to Lattice. Use when asked to make the app sweep something it does not yet sweep, such as a new Camera2 key, or to add a setting that applies to every captured frame.
---

# Adding an axis or a per-frame setting

An axis is **multiplied** into the capture grid. A per-frame setting applies to every frame equally.
Getting this wrong is the most expensive mistake here: adding an axis multiplies frame count, so a
5-value axis makes an existing sweep five times longer.

`plans/WHITE_BALANCE_PLAN.md` is the worked example — white balance was added as the fourth axis and
the plan records both the design and the two places implementation departed from it.

## Before writing code

**Confirm the device can actually do it.** Read the characteristic rather than assuming — see the
`lattice-camera-probe` skill. White balance was only viable because the device reports
`MANUAL_POST_PROCESSING` and `CONTROL_AWB_MODE_OFF`.

**Choose the unit in which equal steps are equal physical change.** This project sweeps focus in
diopters and colour temperature in mired, both reciprocals, because the natural-seeming units
(metres, kelvin) make step size vary by more than 10× across the range. Ask what quantity the
downstream analysis actually cares about, and space uniformly in that.

**Default to inert.** A new axis must default to a single value so existing configurations capture
exactly what they did before. White balance defaults to AUTO, which yields exactly one null value
and multiplies the frame count by one.

## The files that must change together

| File | Change |
|---|---|
| `model/` | The axis type and its `values()`. Keep it free of Android types so it is JVM-testable. |
| `model/SweepConfig.kt` | Add the field **with a default**, expose a resolved value list, extend `totalCaptures`. |
| `camera/CameraController.kt` | Take the value as a parameter of `buildManualRequest`, set the Camera2 key, add the loop level, extend `checkSettled`, record the actual value. |
| `storage/CaptureRecord.kt` | Add requested and **actual** fields; default them so existing call sites compile. |
| `storage/SweepStorage.kt` | Filename token, manifest `config` and `captures` entries, CSV header and row. |
| `storage/ConfigStore.kt` | Bump `SCHEMA_VERSION`, persist the axis, and migrate older payloads rather than discarding them. |
| `ui/MainViewModel.kt` | A `ConfigSection` entry, plus an `Accent` colour. |
| `ui/AxisEditor.kt` | An editor composable and presets. |
| `ui/ConfigureSheet.kt` | A branch in the section `when`, in `resetSection`, and in `sectionImpact`. |
| `ui/ConfigSummaries.kt` | `sectionValue`, `sectionDetail`, `sectionCount`. |
| tests | The generator's invariants, and that the default still multiplies by one. |

`ui/MainScreen.kt` chunks `ConfigSection.entries` into rows of four, so the grid absorbs a ninth
entry as a partial row — check it still looks right.

## Non-obvious requirements

**Record the actual, not just the requested.** Hardware quantizes: exposure snaps to a line-time
step, the focus actuator has a finite grid, colour gains get clamped. Read the value back from the
`CaptureResult` and put it in the manifest. All downstream analysis uses the actual.

**Extend `checkSettled`, and tolerate a null read.** Not every HAL populates every key in every
result. A missing read must not be reported as unsettled — mirror how `lensState` is handled.

**Filename tokens should be omitted when the axis is inert.** The white balance token appears only
when white balance was actually swept, so a default sweep produces exactly the filenames it did
before the axis existed. A renamed output file is an observable change for users who never touched
the feature.

**Persist with a migration.** `ConfigStore` reads defensively and a missing key should resolve to
the inert default, which is what every pre-feature configuration was captured with.

## Verify

Unit tests for the generator, then `lattice-verify` on hardware. The specific checks that matter:

1. A default configuration's `totalCaptures` is unchanged from before the axis existed.
2. The actual values in the manifest differ across frames and move monotonically with the request.
3. Every frame reports `settled: true`.
4. Two frames from opposite ends of the axis are measurably different — proof the key reached the
   sensor rather than being silently ignored.
