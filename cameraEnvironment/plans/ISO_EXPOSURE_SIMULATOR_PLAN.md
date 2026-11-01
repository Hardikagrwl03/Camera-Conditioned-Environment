# ISO / Exposure-Time Simulator — Implementation Plan

Goal: from **one scene raw capture** (DNG, ISO 50, 100 ms, one focus value from the sweep's focus
list) synthesize the frame the phone would have produced at any **ISO ∈ [50, 1600]** and
**exposure ∈ [1 ms, 100 ms]**, and validate it against the real captured frame sweep of the same
scene. Accuracy is the priority; complexity is acceptable.

Both axes are treated on a **log scale** (log2 = EV stops) everywhere: parameter sampling, grids,
plots, error reporting.

---

## 0. Facts established before planning (do not re-derive)

Device: **Samsung SM-S711B**, rear camera 0 (from `../../application/latticeApp/EXPERIMENTS.md`).

| Property | Value |
|---|---|
| Pixel array | 4080 × 3060, 2.0 µm pitch |
| Raw | 10-bit, `whiteLevel = 1023`, black level pattern reported 0,0,0,0 (verify per frame — `BLACK_LEVEL_LOCK` is not set) |
| ISO range | 50 – 3200 (manifest reports `sensitivityRange` 12–3200; trust the CaptureResult) |
| Exposure range | 85 µs – 100 ms |
| Capabilities | MANUAL_SENSOR, MANUAL_POST_PROCESSING, RAW, BURST_CAPTURE |
| Aperture | f/1.8 fixed |

How the sweep PNGs are produced (from the Lattice app source, branch `application`, local clone at
`/home/hamster/projects/Camera-Environment/application/latticeApp`):

- `CameraController.kt` builds each request from `TEMPLATE_STILL_CAPTURE` with
  `CONTROL_MODE_OFF`, `AE_MODE_OFF`, `AF_MODE_OFF`, `SENSOR_SENSITIVITY`, `SENSOR_EXPOSURE_TIME`,
  `LENS_FOCUS_DISTANCE`, `SENSOR_FRAME_DURATION = clamp(exp + 10 ms)`. With manual post-processing:
  `NOISE_REDUCTION_MODE_OFF`, `EDGE_MODE_OFF`. White balance: default path is `AWB_MODE_AUTO` +
  `AWB_LOCK` (frozen gains, recorded per frame as `gainR, gainGEven, gainGOdd, gainB`).
- Output format PNG = **YUV_420_888** from the phone ISP → ARGB via **integer BT.601
  limited-range** (`YuvConverter.kt`, exact formula there, chroma 4:2:0 with nearest-neighbour
  upsampling `col shr 1`, `row shr 1`) → **integer box downscale in gamma domain** with rounding
  `(sum + half) / n` (`Downscaler.kt`) → 8-bit RGBA PNG (alpha always 255).
- Left at HAL defaults and **not recorded**: `TONEMAP_MODE/CURVE`, `SHADING_MODE`,
  `DISTORTION_CORRECTION_MODE`, `COLOR_CORRECTION_ABERRATION_MODE`, `HOT_PIXEL_MODE`,
  `BLACK_LEVEL_LOCK`, `CONTROL_POST_RAW_SENSITIVITY_BOOST`. The PNGs are strongly tone-mapped
  (measured: mean value is far from linear in exposure).
- Sweep folder layout: `framesweeps/<session>/` with `manifest.json` (camera snapshot + `config`
  axis lists + `captures[]`) and `metadata.csv` (`requestedIso, actualIso, requestedExposureNs,
  actualExposureNs, requestedFocusDiopters, actualFocusDiopters, framesAveraged, downscale,
  outputWidth, outputHeight, gain*, awbState, settled, timestampNs`). Filenames like
  `iso0100_exp001000us_fd1p00D_avg1_ds2_0486.png`. **Always use the `actual*` values** (ISO is
  quantised, e.g. 141 → 140; focus 0 D → 0.1 D).
- The three sweeps currently in `framesweeps/` are **dummies** showing the format. The real sweeps
  will differ (grid values, downscale, averaging, maybe WB axis). **Nothing may be hard-coded to the
  dummy grid** — everything is read from `manifest.json` / `metadata.csv`.

Raw input contract (from the user): `rawframes/<same name as sweep>/` holds the scene DNG at
ISO 50 / 100 ms at one of the sweep's focus values. The plan extends this folder with optional extra
files (burst, bracket, paired PNGs, validation sub-grid) produced by the capture test below; the
simulator must work with the single DNG alone and use the extras when present.

### Why the physics works in one direction

Source exposure product = 50 × 100 ms, at the **maximum** exposure time of the range. Every target
`(ISO, t)` has `t ≤ t0`, so the target's photon count is a **subset** of the source's
(binomial thinning of a Poisson process is again Poisson — exact). Higher ISO is gain applied to
those electrons plus ISO-dependent read noise. The simulator never needs photons the source does
not have. The only unrecoverable information is **source-clipped pixels** (raw ≥ white level):
those are masked, or filled from a raw exposure bracket when available.

---

## 1. Deliverables and repository layout

Two git repos/branches of `github.com:Hardikagrwl03/Camera-Conditioned-Environment`:

### 1a. Branch `environment` — `/home/hamster/projects/Camera-Environment/environment/cameraEnvironment/`

```
cameraEnvironment/
  README.md                         full user guide: Windows step, Linux step, outputs, limitations
  requirements.txt                  linux/GPU side (torch, numpy, scipy, rawpy, tifffile, opencv-python-headless, scikit-image, matplotlib, pytest)
  requirements-windows.txt          windows side (stdlib only if possible; numpy optional)
  .gitignore                        framesweeps/, rawframes/, calibration/, outputs/, device/, *.dng, __pycache__, etc.
  plans/ISO_EXPOSURE_SIMULATOR_PLAN.md   (this file)
  camsim/                           importable package
    __init__.py
    params.py        log-scale helpers: ev(), log-uniform sampling, range validation (ISO 50–1600, 1–100 ms)
    io/sweep.py      Sweep class: parse manifest+metadata generically, index captures by actual (iso, exp, focus, wb),
                     load PNG → float RGB (drop alpha), nearest-focus lookup, list of grid points at a given focus
    io/dng.py        RawFrame dataclass + loader (rawpy for bayer; tifffile/exif for tags): CFA pattern, black level
                     (per-channel + dynamic), white level, AsShotNeutral, ColorMatrix1/2, ForwardMatrix1/2,
                     CalibrationIlluminant, NoiseProfile, BaselineExposure, OpcodeList2/3 (GainMap = lens shading,
                     WarpRectilinear = distortion) if present, exposure/ISO from EXIF + sidecar JSON
    io/sidecar.py    read the per-capture JSON written by the capture test (full CaptureResult dump)
    sensor/model.py  SensorModel (torch, GPU, batched): electrons ↔ DN, gain(ISO), read noise(ISO) (Gaussian or
                     Tukey-lambda), row noise, dark current & DSNU(t, ISO), hot pixels, 10-bit quantisation,
                     clipping at white level, black level. Deterministic seeding.
    sensor/source.py SceneRadiance: build the best radiance estimate from rawframes/<sweep>/:
                     single DNG → as is; burst → mean (noise-reduced); bracket → inverse-variance HDR merge in
                     linear DN/s; clip mask. Records which mode was used.
    sensor/calibrate.py  fit SensorModel parameters: photon-transfer curve (mean–variance from flat-field pairs,
                     difference method), gain per ISO and ISO→gain law (detect where analog gain stops and digital
                     gain starts via histogram gaps), read noise per ISO from dark frames, row noise, dark current
                     vs t, DSNU/hot-pixel maps. Fallback chain: calibration data → DNG NoiseProfile → defaults
                     (warn loudly). Output: calibration/<device>/sensor_calibration.json (+ .npy maps).
    render.py        RawSimulator: (SceneRadiance, SensorModel) → simulated linear raw at (iso, t) [batched, GPU]
    isp/pipeline.py  ISPModel (torch): black level → lens-shading gain map → WB gains → demosaic → CCM →
                     tone curve (per-channel monotone LUT or HAL TONEMAP_CURVE) → RGB→YUV BT.601 limited →
                     4:2:0 chroma (match HAL: average or decimate — determine from pairs) → 8-bit quantise →
                     exact integer yuv420ToArgb replica → exact integer boxDownscale replica (factor from manifest)
    isp/fit.py       fit ISPModel stages from paired (DNG, PNG) captures of the SAME request; report per-stage
                     fit quality; detect whether the tone curve depends on ISO/exposure/scene (local tone mapping)
    isp/residual.py  OPTIONAL learned residual (small CNN conditioned on log2 ISO, log2 t) trained on calibration
                     pairs from scenes other than the evaluation scene; off by default, enabled by flag
    align.py         geometric mapping raw-ISP output ↔ sweep PNG (orientation, crop, scale, distortion);
                     estimated once per device from pairs (ECC / feature homography + optional polynomial warp),
                     verified per scene; saved to calibration/<device>/alignment.json
    simulator.py     public API: CameraSimulator.from_dirs(sweep_dir, raw_dir, calib_dir)
                       .render(iso, exposure_s, return_raw=True) -> {"raw": tensor, "png": tensor, "clip_mask": ...}
                       .render_batch(isos, exposures)
    metrics.py       image metrics (see §5) incl. Neural3A reward terms re-implemented locally
    report.py        plots + HTML/Markdown report
  scripts/
    windows/collect_device_info.py   adb dumpsys media.camera + getprop → device/<model>/device_profile.json (+ raw dump)
    windows/capture.py               build/install/run the Android capture test via gradle+adb, pull results into
                                     rawframes/<sweep>/ and calibration/<device>/raw_captures/
    fit_calibration.py               linux: sensor + ISP + alignment fitting → calibration/<device>/
    simulate_sweep.py                linux: render every sweep grid point at the raw frame's focus → outputs/<sweep>/
    validate.py                      linux: compare simulated vs real, metrics + plots + report → outputs/<sweep>/report/
    run_all.sh                       linux one-shot: fit → simulate → validate for every sweep that has rawframes
  tests/                             pytest, synthetic data only (no device, no real DNG needed)
```

### 1b. Branch `application` — `/home/hamster/projects/Camera-Environment/application/latticeApp/`

One new instrumentation test, e.g.
`app/src/androidTest/java/dev/hamster/lattice/RawCalibrationCaptureTest.kt`, driven by
instrumentation arguments. **Do not touch or commit the user's untracked
`ExposureCeilingProbeTest.kt`**; read it as the reference for how a Camera2 capture runs inside an
instrumentation test (session setup, handler thread, permissions, output location).

---

## 2. Android capture test (`application` branch)

Runs via `adb shell am instrument -w -e <args> dev.hamster.lattice.test/androidx.test.runner.AndroidJUnitRunner`
(confirm the runner/test package from `app/build.gradle.kts`).

Requirements:
1. **Identical request construction to the sweep.** Replicate `CameraController`'s manual request
   exactly (template, 3A off, AWB lock behaviour, NR/EDGE off, frame-duration rule, clamping).
   Better: call the app's own code if it is reachable from the test without the UI; otherwise
   duplicate it and add a comment pointing to the source lines.
2. **Paired outputs from one request:** RAW_SENSOR ImageReader (→ `DngCreator` → `.dng`) **and**
   YUV_420_888 ImageReader (→ the app's `yuv420ToArgb` + `boxDownscale` with the sweep's factor →
   `.png`, written exactly as the app writes sweep PNGs). Also save full-res PNG (factor 1) for ISP
   fitting. Note: adding a RAW stream can change HAL behaviour vs. the YUV-only sweep session; mode
   `yuvonly` below exists to measure that.
3. **Full metadata sidecar** per capture (`<name>.json`): every `CaptureResult` key available,
   especially SENSOR_SENSITIVITY, SENSOR_EXPOSURE_TIME, LENS_FOCUS_DISTANCE, SENSOR_NOISE_PROFILE,
   SENSOR_DYNAMIC_BLACK_LEVEL, SENSOR_DYNAMIC_WHITE_LEVEL, COLOR_CORRECTION_GAINS/TRANSFORM,
   TONEMAP_MODE/CURVE (request `TONEMAP_CURVE` readback), SHADING_MODE, lens shading map
   (`STATISTICS_LENS_SHADING_MAP_MODE_ON` — only if enabling it does not alter processing; record it
   in a separate dedicated capture if unsure), DISTORTION_CORRECTION_MODE, HOT_PIXEL_MODE,
   CONTROL_POST_RAW_SENSITIVITY_BOOST, SENSOR_TIMESTAMP, SENSOR_TEMPERATURE if any. Also dump
   `CameraCharacteristics` once per session.
4. **Modes** (argument `mode`), all with configurable lists (`isos`, `exposuresNs`, `focusD`,
   `frames`, `downscale`, `session` name, `outDir`):
   - `scene` — run immediately after the sweep, tripod untouched. Defaults: burst of N=16 DNG(+PNG)
     at ISO 50 / 100 ms at the given focus (the "raw frame"; frame 0 is the canonical one); ISO 50
     raw bracket at 100, 25, 6.25, 1.5625 ms × 4 frames; **validation sub-grid** DNG+PNG at
     ISO {100, 400, 1600} × {1, 10, 100} ms (raw-domain ground truth); 2 repeated PNGs at a few
     sweep points (real-vs-real noise floor).
   - `dark` — lens covered: per ISO in list × exposures {1, 10, 100 ms} (configurable) × 4 frames, DNG only.
   - `flat` — uniform diffuse target, per ISO: exposure ramp (log-spaced, ~12 values up to near
     saturation) × 2 frames, DNG only (PTC difference method).
   - `pairs` — ISP fitting on other scenes: DNG+PNG pairs over a log-spaced ISO×t sub-grid.
   - `yuvonly` — YUV-only session at a few settings to check paired captures match sweep PNGs.
5. Output under the app's external files dir (no all-files permission needed) or `/sdcard/Lattice/raw/<session>/`
   — follow what `ExposureCeilingProbeTest` / the app already do. Filenames follow the sweep
   convention, e.g. `iso0050_exp100000us_fd1p00D_raw_f00.dng`, plus a `capture_manifest.json`.
6. Must compile: `./gradlew :app:assembleDebugAndroidTest` (Android SDK is at `~/Android/Sdk`).
   It cannot be run here (no device) — make failure messages explicit.

---

## 3. Windows-side scripts (`scripts/windows/`)

Python 3.10+, **stdlib only** (subprocess + json); `adb` found via PATH, `ANDROID_HOME`, or `--adb`.
Must work in PowerShell/cmd: use `pathlib`, no shell-specific syntax, no `chmod`, handle `\r\n`.

- `collect_device_info.py`: `adb devices` check (exactly one device or `--serial`), `getprop`
  (model, build), `dumpsys media.camera` → save raw text, parse the static characteristics we need
  (black level pattern, white level, color transforms, forward matrices, reference illuminants,
  noise profile if static, available tonemap/shading/hot-pixel/distortion modes,
  postRawSensitivityBoostRange, lens shading map size, physical size) into `device_profile.json`.
  Parser must be tolerant (missing keys → null + warning).
- `capture.py` subcommands mirroring the test modes (`scene --sweep 20260911_144103 --focus 1.0`,
  `dark`, `flat`, `pairs --name <scene>`, `yuvonly`): build+install app & test APKs via the app
  repo's gradle wrapper (`--app-dir`, default `../../application/latticeApp` relative to the repo,
  `--skip-build`), grant permissions via `adb shell pm grant`, run `am instrument`, stream and parse
  its output, fail loudly on test failure, `adb pull` results into `rawframes/<sweep>/` or
  `calibration/<device>/raw_captures/<mode>/...`, verify file counts vs `capture_manifest.json`,
  optionally delete from device (`--cleanup`). For `scene`, default focus = must be a value from the
  sweep's `focusValuesDiopters` (validate against the manifest, error otherwise), and the sweep
  session name must exist in `framesweeps/`.
- Print a short checklist at the end (what was captured, sizes, next step: copy folder to server).

The whole `cameraEnvironment/` folder (with `framesweeps/`, `rawframes/`, `calibration/`, `device/`)
is then copied to the Linux server.

---

## 4. Simulation model (Linux, `camsim/`)

All tensors in torch, batched over (iso, t), GPU if available, CPU fallback. Work at full sensor
resolution in raw; downscale only at the end, exactly as the app does.

### 4.1 Sensor forward model (per Bayer pixel, DN above black)

```
source:     S0 = raw0 - black0                    (DN at ISO0=50, t0=100 ms)
electrons:  e0 = S0 / g(ISO0)                      g = DN per electron (calibrated)
            target mean  μ = e0 · t / t0
shot:       single-frame source → e = Binomial(round(e0), t/t0)   (exact Poisson thinning)
            averaged/merged source → e = Poisson(μ)
dark:       + dark current(t, ISO) + DSNU map(t, ISO)  (subtract source's, add target's)
gain:       D = g(ISO) · e
read:       + read noise(ISO)  (Gaussian or Tukey-λ per ELD; + row noise)
quantise:   round to integer DN; + black level; clip to [0, white]
```

- Do **not** add PRNU / lens shading: they are already in the source signal (multiplicative).
- `g(ISO)` from PTC calibration; do not assume `g ∝ ISO` — verify, and model digital gain
  (quantisation gaps) above the analog-gain ceiling if detected. ISO 50 may itself be special
  (below the sweep's base 100) — check its PTC separately.
- Source read noise: single-frame source carries its own read noise; account for it (subtract its
  variance budget where possible; document the residual error). Burst-averaged source removes it.
- Clip handling: pixels with source raw ≥ white − margin → `clip_mask`. Fill from bracket if
  available (HDR merge), else propagate the mask to evaluation and report the masked fraction.
- `CONTROL_POST_RAW_SENSITIVITY_BOOST`: if the sidecars show it ≠ 100, model it in the ISP stage.

### 4.2 ISP model (raw → sweep-equivalent PNG)

Stages in §1 `isp/pipeline.py`. Fit each from `pairs` + `scene` pair data, **never** from the
evaluation sweep frames themselves (the scene's ISO50/100 ms pair is allowed — it is part of the raw
input, not the sweep). Use HAL-provided values where they exist (WB gains from sweep metadata,
COLOR_CORRECTION_TRANSFORM, TONEMAP_CURVE, lens-shading map), fit the rest:
demosaic choice, chroma subsampling method, tone LUT if the HAL curve is unavailable, any residual
gamma. Then test whether a global tone curve is sufficient: compare LUT fits across ISO/t/scenes;
if they differ (local / adaptive tone mapping), enable `isp/residual.py`, validated with
leave-one-scene-out.

### 4.3 Alignment

Raw and YUV are both in sensor orientation, but distortion correction / crop / chroma-aberration
correction may resample the YUV. Estimate the raw→PNG geometric map from pairs once per device,
save it, apply it in the ISP, and report residual misalignment (px) on the scene pair.

---

## 5. Validation against the sweep (`scripts/validate.py`)

For every sweep capture at the raw frame's focus (match on `actualFocusDiopters`; error if the raw's
focus is not in the sweep), with `actualIso ∈ [50,1600]`, `actualExposure ∈ [1,100] ms`:

1. Render a **noise-free mean** image and **K noisy realisations** (default K=4) at the actual ISO/t.
2. Metrics, computed on the registered, valid (non-clipped) region, full frame and centre crop:
   - Brightness on log scale: per-channel mean and median error in **EV** (`log2(sim/real)` on
     linearised values) and in 8-bit DN; histogram distance (EMD per channel).
   - Pixelwise: PSNR, SSIM, MAE of the noisy sim vs real, and of real-vs-real repeated frames where
     available (**noise floor** — a perfect simulator matches this, not ∞ PSNR).
   - Noise: noise-level function (local variance vs local mean, binned) sim vs real; noise power
     spectrum ratio.
   - Neural3A reward terms (midtone, tenengrad, sobel) re-implemented in `camsim/metrics.py`, with
     the formulas copied from `/home/hamster/projects/Neural3A/Neural-3A-SAC/src/neural3a/rewards/reward.py`
     (cite in a docstring; no import dependency). Same 150×150 crop convention as Neural3A for
     these.
3. **Raw-domain validation** when the scene's validation sub-grid DNGs exist: simulated raw vs real
   raw — mean (EV) error per channel, variance vs mean, read-noise floor — this isolates sensor
   physics from ISP errors.
4. Report: `metrics.csv` (one row per grid point), `summary.json`, plots with **log2 axes**:
   brightness vs log2 exposure per ISO (sim vs real), EV-error heatmap over (log2 ISO × log2 t),
   PSNR heatmap with noise-floor reference, noise-level-function overlays, side-by-side crops
   (real | sim | diff ×4) for a 3×3 subset, and a single `report.html` (self-contained, base64
   images). Print a pass/fail table against thresholds in a config (defaults: |EV error| ≤ 0.05,
   PSNR within 1 dB of noise floor, reward-term relative error ≤ 5 %) — thresholds are reported,
   not enforced.

`scripts/simulate_sweep.py` writes `outputs/<sweep>/sim_png/<same filename as sweep>` and
`outputs/<sweep>/sim_raw/*.tiff` (16-bit) so outputs drop straight into Neural3A's FrameSweep
format (also write a `manifest.json`/`metadata.csv` in the same schema).

---

## 6. Tests (`tests/`, synthetic only)

- Thinning: synthetic Poisson source → thinned mean/variance match theory (χ² / tolerance).
- Gain/read noise: simulated variance-vs-mean slope = g, intercept = read²; recover via calibrate.py
  on synthetic flat pairs (round-trip).
- Clipping & masks; exposure > t0 rejected; log-uniform sampling stays in range.
- Exact replicas: `yuv420ToArgb` and `boxDownscale` Python ports vs hand-computed values copied
  from the Kotlin integer formulas (golden vectors).
- Sweep parser on the dummy `framesweeps/` (skip if absent) and on a tiny synthetic manifest with
  a different grid (proves nothing is hard-coded).
- End-to-end smoke test: synthetic RawFrame (no DNG file) → render → ISP → metrics, on CPU.
- DNG loader: test with a DNG written by `tifffile` if feasible; otherwise mock rawpy.

---

## 7. Operating procedure (goes in README)

**Windows + phone (capture day, per scene):**
1. Mount phone on tripod, run the Lattice sweep as usual.
2. Without touching the rig: `python scripts/windows/capture.py scene --sweep <session> --focus <D>`.
3. Pull the sweep folder into `framesweeps/<session>/` (existing workflow).

**Windows + phone (once per device, any day):**
4. `python scripts/windows/collect_device_info.py`
5. `capture.py dark` (lens covered), `capture.py flat` (uniform target), `capture.py pairs --name <scene>`
   for ≥ 3 varied scenes, `capture.py yuvonly`.

**Linux GPU server:**
6. Copy the whole folder; `pip install -r requirements.txt`.
7. `bash scripts/run_all.sh` (or `fit_calibration.py`, `simulate_sweep.py --sweep <s>`, `validate.py --sweep <s>`).
8. Open `outputs/<sweep>/report/report.html`.

Degraded modes (must still run, with warnings in the report): no calibration → DNG NoiseProfile +
global tone curve fitted from the scene's own ISO50/100 ms pair; single DNG, no burst/bracket.

---

## 8. Known limitations to document

- Single-frame source noise is partially baked into the source; burst averaging fixes it.
- Source-clipped highlights are unrecoverable without the bracket.
- Phone ISP may be scene-adaptive; a global tone curve may not be enough → learned residual.
- Static scene assumed (no motion blur modelling for long exposures).
- Raw+YUV paired sessions may differ from YUV-only sweep sessions → measured by `yuvonly`.
- Focus is fixed to the raw frame's focus; the focus axis is out of scope here.
