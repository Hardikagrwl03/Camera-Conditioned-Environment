# Lattice — User Guide

Lattice takes the camera off automatic and sweeps a grid of settings, saving one frame per point
along with a record of what the sensor actually applied.

If you want the reasoning behind *why* each axis is sampled the way it is — the optics, the
hardware probes and the formulas — see [EXPERIMENTS.md](EXPERIMENTS.md). It is built for a fixed rig: put the phone
on a mount, point it at your subject, and let it work through every combination you asked for.

---

## 1. Before your first sweep

On first launch Lattice shows a permission screen with a button for each of the two it needs:

- **Camera** — for the live preview and the capture itself.
- **All files access** — to write sessions to `/sdcard/Lattice`, which is outside the app's own
  private storage. The button opens the system screen for it; toggle it on and return to Lattice.
  The app notices as soon as you come back.

The main screen appears once both are granted.

Mount the phone before you start. A sweep can run for minutes and every frame after the first
should see the same scene, otherwise the settings axes are confounded with camera shake.

Check free space. Lattice estimates the sweep size up front and refuses to start if there is not
enough room, but it is easier to clear space before you have configured everything.

---

## 2. The main screen

The preview fills the top. Below it are eight tiles in two rows:

| Row | Tiles | What they are |
|---|---|---|
| Top | ISO · SHUTTER · FOCUS · WB | The four **sweep axes**. Every combination is captured. |
| Bottom | AVERAGE · SETTLE · SCALE · FORMAT | **Per-frame settings**. They apply to every capture equally. |

Each tile shows its current state. Tap one to open its editor as a panel over the bottom of the
screen. **Changes apply the moment you close the panel** — there is no Apply button. Each panel has
its own **Reset**, which restores only that setting.

The pill under the shutter button is the running total: how many frames the current configuration
will produce and roughly how long it will take.

> **The four axes multiply.** The defaults — 9 ISO values × 9 shutter speeds × 10 focus distances
> × 1 white balance — are 810 frames, not 29. Watch the pill as you configure; it is easy to build
> a sweep that runs for hours.

---

## 3. The four sweep axes

Three of the four share the same two modes:

- **List** — you type the exact values, comma separated.
- **Uniform** — you give endpoints and a count, and Lattice spaces the values evenly in whichever
  unit is physically meaningful for that axis.

### ISO

Sensor gain. Uniform mode spaces values geometrically, so each step is a constant ratio — the same
way photographers think in stops. Presets cover the full supported range, the low half, and the
sensor's native ISO alone.

Use the native ISO for the cleanest frames; sweep the range when you are characterising noise.

### Shutter

Exposure time, entered and displayed in **milliseconds**. Uniform mode is geometric here too, for
the same reason as ISO.

The default spans **1–100 ms in 9 steps**, clamped to whatever the sensor actually reports. That
range is a practical starting point rather than the widest possible sweep — very long exposures make
a sweep slow. Type wider values by hand in either mode, up to the sensor's limits.

### Focus

Focus distance, in **diopters** (1/metres): `0 D` is infinity, `10 D` is 10 cm.

**Uniform mode divides the range into four distance bands** — 100 m–∞, 10–100 m, 1–10 m and
0.1–1 m — and you set a count for each. Two things to understand:

- **The band edges are always captured.** ∞, 100 m, 10 m, 1 m and the lens's closest focus are in
  every set. A band's count is how many extra values it adds *between* its two edges, so `0` means
  "just the edges".
- **Bands are spaced geometrically in distance**, so a count sets the ratio between neighbouring
  distances rather than a fixed number of metres. The 100 m–∞ band is the exception and is linear
  in diopters, because distance has no finite end there.

Presets: **Equal**, **Near**, **Mid**, **Far**. Each concentrates the samples on one part of the
scene while keeping every boundary, so no distance range is ever dropped entirely.

Counts are capped at what the lens can physically resolve. The 100 m–∞ band spans only 0.01 D —
about two actuator steps — so it accepts at most one value between its edges. Asking for more
would return duplicate frames, and the editor tells you so.

### White balance

**Auto is the default and its own switch, above the mode selector.** Leave it on and Lattice
touches no colour keys at all: the sweep captures exactly what it would have without this axis, and
adds no frames.

Switch Auto off to sweep colour temperature in kelvin. Uniform mode spaces values evenly in
**mired** (10⁶/K), not kelvin, because that is the unit in which equal steps are equal colour
shifts — a 1 000 K step at 2 000 K is a large change, the same step at 9 000 K is barely visible.
Range is 2 000–25 000 K. Presets: **Full range**, **Indoor**, **Daylight**.

> Kelvin here is a *nominal* label on a family of channel gains, derived from a black-body
> approximation rather than this sensor's own colour calibration. It gives you a smooth, monotonic,
> repeatable spread of white balances — not a colorimetrically correct rendering of that
> illuminant. **Analyse using the gains recorded in the manifest, not the requested kelvin.**

---

## 4. The four per-frame settings

### Average (1–64)

Captures this many frames per configuration and averages them, reducing sensor noise. It multiplies
capture time but **not** the number of files — you still get one output per grid point.

With JPEG the averaged result is re-encoded, adding a second generation of compression loss on top
of the noise you just removed. The editor offers to switch you to PNG when you raise this above 1.

### Settle (0–10)

Warm-up frames discarded after each settings change, so the sensor and lens have applied the new
values before the frame you keep. The default is 1, which is enough for the electronic axes; raise
it when the lens has a long way to travel.

At 0, the first frame after each change may still carry the previous settings. Raise it if the
manifest shows frames marked `settled: false`.

### Scale (up to 10×, default 2×)

Box-downscales each frame before saving, averaging blocks of pixels. Only factors that divide the
frame size exactly are offered, so no pixels are ever cropped. The 2× default trades resolution for
a quarter of the file size, which suits most work and also lowers noise.

> **Set Scale to 1× for a focus sweep.** The lens's diffraction spot is about 1.2 pixels wide, so
> even 2× binning undersamples the blur a focus sweep exists to measure — and 2× is the default, so
> this is a change you have to make deliberately. Scale is for ISO and shutter work, where
> resolution is not the variable.

### Format

- **PNG** (default) — lossless encode of the uncompressed frame, roughly 30 MB per full-resolution
  frame. No compression artifacts.
- **JPEG** — the camera's own encoder, roughly 5 MB. Lossy: chroma subsampling and DCT
  quantization.

At the default 2× scale a PNG frame is nearer 7.5 MB, since size falls with the square of the
factor. PNG is the default because artifact-free frames matter more than disk for this kind of
work, but a large sweep can still exhaust storage — switch to JPEG while you are iterating.

Neither is sensor RAW. Both are processed 8-bit output — tonemapped and gamma-encoded — so pixel
values are **not** linear in scene radiance. Keep that in mind before doing photometry on them.

---

## 5. Running a sweep

Press the shutter button. Lattice checks free space, creates a session directory, and works
through the grid, showing progress and the settings currently being applied.

The order is focus outermost, then white balance, then ISO, then shutter — slowest-changing thing
first, so the lens moves as little as possible.

**Cancelling is safe.** Stop a sweep part-way and the manifest and CSV are still written, covering
everything that was captured up to that point. A partial session is a valid dataset, not a
corrupted one. The same holds if the sweep fails.

---

## 6. What you get

Each sweep writes one directory:

```
/sdcard/Lattice/20260906_213517/
├── iso0400_exp020000us_fd1p00D_avg1_ds1_0000.jpg
├── iso0400_exp020000us_fd1p00D_avg1_ds1_0001.jpg
├── …
├── manifest.json
└── metadata.csv
```

### Filenames

```
iso<ISO>_exp<MICROSECONDS>us_fd<DIOPTERS>D[_wb<KELVIN>K]_avg<N>_ds<FACTOR>_<INDEX>.<ext>
```

Every parameter is in the name, so a frame is identifiable without opening anything. The decimal
point in the focus value is written as `p` — `fd1p00D` is 1.00 D.

The `_wb…K` token **only appears when white balance was actually swept**. On Auto it is omitted
entirely, so filenames match what earlier versions of the app produced.

### `manifest.json`

The complete record, in three parts:

- **`camera`** — the sensor's reported ranges and capabilities, including `sensorOrientation`,
  which you need to orient the images correctly.
- **`config`** — the resolved value list for every axis, plus format, scale, averaging and totals.
- **`captures`** — one entry per frame, with both what was requested and what actually happened.

### `metadata.csv`

The same per-frame data, flat, for loading straight into analysis:

```
index, filename,
requestedIso, actualIso,
requestedExposureNs, actualExposureNs,
requestedFocusDiopters, actualFocusDiopters,
framesAveraged, downscale, outputWidth, outputHeight,
requestedKelvin, gainR, gainGEven, gainGOdd, gainB, awbState,
settled, timestampNs
```

---

## 7. Reading the results

**Always analyse the `actual` columns, never the `requested` ones.** Hardware quantizes: exposure
snaps to a line-time step, the focus actuator has a finite grid, and colour gains may be clamped.
Requesting 1.000 D might land at 0.999039 D. The requested value says what you asked for; the
actual value says what the frame contains.

**`settled` is your quality flag.** `true` means the sensor confirmed the requested ISO, exposure,
focus and colour gains before the frame was kept, and the lens had stopped moving. Frames marked
`false` may carry settings in transition — filter them out, or re-run with a higher Settle count.

**`awbState`** tells you what white balance was doing: on Auto you will see the locked state, and
on a manual sweep it reads inactive because the automatic algorithm is switched off.

---

## 8. Things worth knowing

**Auto white balance is inherited, not live.** Manual sensor control switches Android's whole 3A
pipeline off. On Auto, the frames carry whatever white balance the camera had settled on *before*
the sweep began, then held fixed. This is stable across a sweep, which is what you want, but it
means the balance depends on what the preview was looking at when you pressed the shutter. Let the
preview settle on your scene first.

**Your configuration persists** across app restarts, but only for the same camera. It is stored
with a fingerprint of the sensor's ranges, so it will not be restored onto different hardware where
the values would be meaningless.

**Estimates are approximations.** The frames and duration in the pill assume ~5 MB per JPEG and
~30 MB per PNG, divided by the square of the scale factor. Real sizes vary with scene content.

**Sessions are never overwritten.** Each is named by the second it started.

---

## 9. Some worked configurations

The defaults are a general-purpose mesh: **9 ISO × 9 shutter × 10 focus × Auto = 810 frames**, at
2× scale in PNG. Each recipe below changes only what it lists.

| Goal | ISO | Shutter | Focus | WB | Frames | Notes |
|---|---|---|---|---|---|---|
| Noise characterisation | default, 9 | fixed, 1 | fixed, 1 | Auto | 9 | Raise Average to 8–16. Scale is safe here and helps. |
| Exposure response | fixed, 1 | default, 9 | fixed, 1 | Auto | 9 | **Set Scale to 1×** if you care about per-pixel values. |
| Focus / depth survey | fixed, 1 | fixed, 1 | default, 10 | Auto | 10 | **Set Scale to 1×.** Raise Settle to 3–4 for long lens travel. |
| Colour response | fixed, 1 | fixed, 1 | fixed, 1 | Uniform, Full range | 9 | Candlelight to deep shade in equal mired steps. |
| Dense focus survey | fixed, 1 | fixed, 1 | List, 86 values at 0.1173 D | Auto | 86 | Gap-free in blur. **Scale 1×.** See EXPERIMENTS.md §4.2. |
| Full mesh | default, 9 | default, 9 | default, 10 | Auto | 810 | The defaults as shipped. |

To pin an axis to a single value, switch it to **List** and type one number.
