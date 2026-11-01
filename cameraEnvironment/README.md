# Camera ISO / exposure-time simulator (`camsim`)

From **one scene raw capture** (DNG, ISO 50, 100 ms, one focus of the sweep) synthesise the frame the
phone would produce at any **ISO in [50, 1600]** and **exposure in [1 ms, 100 ms]**, and validate it
against the real Lattice frame sweep of the same scene. Both axes are handled on a log scale (log2 =
stops) for sampling, grids, plots and error reporting. Full plan: `plans/ISO_EXPOSURE_SIMULATOR_PLAN.md`.

## Why the physics works in one direction
The source exposure product is 50 x 100 ms, at the *maximum* exposure. Every target has t <= t0, so its
photons are a binomial thinning of the source's (exact for Poisson). Higher ISO is gain plus ISO
dependent read noise. The only unrecoverable information is source-clipped pixels (masked, or filled
from the raw exposure bracket).

## Layout
- `camsim/` package: `io/` (sweep, DNG, sidecar), `sensor/` (model, source, calibrate), `isp/` (pipeline,
  fit, residual), `align.py`, `render.py`, `simulator.py`, `metrics.py`, `validation.py`, `report.py`.
- `scripts/windows/` capture-day scripts (stdlib only); `scripts/*.py`, `run_all.sh` Linux pipeline.
- `tests/` synthetic-only pytest suite (CPU, seconds).
- Android side: `RawCalibrationCaptureTest.kt` on branch `application` of the Lattice app repo.

## Operating procedure
**Windows + phone, per scene** (capture day)
1. Mount the phone on a tripod, run the Lattice sweep as usual.
2. Without touching the rig: `python scripts/windows/capture.py scene --sweep <session> --focus <D>`
   (focus must be a value in the sweep's `focusValuesDiopters`; the session must already exist in `framesweeps/`).
   This builds/installs the app + test APK (needs the `application` clone, `--app-dir`), runs the test and pulls
   `rawframes/<session>/`.
3. Pull the sweep folder into `framesweeps/<session>/` (existing workflow).

**Once per device**
4. `python scripts/windows/collect_device_info.py` -> `device/<model>/device_profile.json`.
5. `capture.py dark` (lens covered), `capture.py flat` (uniform diffuse target), `capture.py pairs --name <scene>`
   for >= 3 varied scenes, `capture.py yuvonly`. Results go to `calibration/<model>/raw_captures/...`.

**Linux GPU server**
6. Copy the whole folder, `pip install -r requirements.txt`.
7. `bash scripts/run_all.sh` or individually `scripts/fit_calibration.py --device-name <model> --include-scene-pairs`,
   `scripts/simulate_sweep.py --sweep <s>`, `scripts/validate.py --sweep <s>`.
8. Open `outputs/<sweep>/report/report.html`.

Outputs: `outputs/<sweep>/sim_png/<same filenames as the sweep>`, `sim_raw/*.tiff` (16-bit), `manifest.json` +
`metadata.csv` in the sweep schema (drop-in for Neural3A's FrameSweep), and `report/` with `metrics.csv`,
`summary.json`, `report.html` (self-contained). `--dense N_ISO N_EXP` renders a log-uniform grid instead.

Degraded modes (still run, with warnings in the report): no calibration -> DNG NoiseProfile + a tone curve fitted
from the scene's own ISO50/100 ms pair (or the sRGB fallback if no pair); single DNG without burst/bracket.

## Raw capture folder format
`rawframes/<session>/` holds `iso0050_exp100000us_fd1p00D_raw_f00.dng` (+ `.json` full CaptureResult sidecar, `.png`
sweep-equivalent PNG, `_full.png`) and `capture_manifest.json`. Tags: `raw` burst (frame 0 canonical), `brk` raw
bracket, `val` validation sub-grid, `rep` repeated PNGs (noise floor), `lsm` lens-shading-map capture. A lone DNG works too.

## Design decisions (choices the plan left open)
- **Thinning**: single-frame source -> exact `Binomial(round(e0), t/t0)`; burst/bracket source -> `Poisson` of the
  merged mean. The source's baked read noise survives thinning with variance p^2 sigma_s^2 and is *subtracted from
  the target's read-noise budget*; only `max(sigma_t^2 - p^2 sigma_s^2, 0)` is added. Residual excess (when baked noise
  exceeds the target's) is not removable. At (ISO 50, 100 ms) the render equals the source up to integer rounding of e0.
- **Gain law**: calibrated per ISO (log-log interpolation), not assumed proportional; default (no calibration) is
  proportional to ISO with read noise constant in electrons (NoiseProfile S, O -> g = S*range, read = sqrt(O)*range).
  Digital gain above `analog_iso_max` (detected from histogram gaps) is applied on the quantised value.
- **Read noise** Gaussian by default; Tukey-lambda available (`read_dist="tukey"`). Row noise and dark current / DSNU
  / hot pixels are subtracted for the source and re-added for the target; no PRNU/shading is added (already in the source).
- **Clip handling**: pixels within 0.5 % of range of white level in the source are masked; the bracket merge is
  inverse-variance in DN/s and only uses unclipped samples. The mask is dilated by the 5x5 demosaic footprint on the PNG grid.
- **ISP**: Malvar-He-Cutler demosaic; CCM from HAL `COLOR_CORRECTION_TRANSFORM` (sidecar) else DNG ForwardMatrix else
  inverted ColorMatrix; shared monotone tone LUT over sqrt(linear) (fitted; sRGB OETF only as a flagged fallback);
  BT.601 limited YUV 4:2:0 (chroma method picked from full-res pairs), then exact integer replicas of
  `yuv420ToArgb` and `boxDownscale`. CCM refinement only with >= 3 pairs (otherwise the HAL matrix is trusted) and
  reverted if it does not improve hold-out error. Per-channel LUT only with >= 3 pairs.
- **Alignment**: ECC on tone-invariant gradient maps, translation first then affine; accepted only if NCC improves and
  the parameters are plausible, otherwise identity (pure size scaling).
- **Metrics**: EV error uses sRGB-linearised PNG values (an approximation of linear light; the raw-domain validation is
  the exact one). Pixels below 0.4 % linear are dropped from EV. Points with > 80 % simulated clipping are excluded from
  pass/fail. Thresholds are reported, not enforced.
- **Android test** reuses the app's `yuv420ToArgb`, `boxDownscale`, `encodePixels`, `CameraCapabilitiesReader`; the manual
  request is duplicated from `CameraController.buildManualRequest` (private). A 45-frame auto-AWB pre-roll runs first so the
  frozen gains are the scene's; gains used are in every sidecar and compared with the sweep's (warning if > 3 % apart).

## Learned residual
`camsim/isp/residual.py` (identity-initialised CNN conditioned on log2 ISO / log2 t) exists and is unit-tested, but is
off by default; `fit_calibration.py` reports `tone_varies` and recommends it when per-pair tone curves differ > 3 DN.
Training on real pairs / leave-one-scene-out validation is not automated yet.

## Known limitations
- Single-frame source noise is partly baked into the source; burst averaging fixes it. The "noise-free mean" of a
  single-frame source still contains the (scaled) source noise.
- Source-clipped highlights are unrecoverable without the bracket.
- The phone ISP may be scene-adaptive; a global tone curve may not suffice.
- Static scene assumed; focus fixed to the raw frame's focus.
- Raw+YUV paired sessions may differ from the YUV-only sweep session (`capture.py yuvonly` measures it).
- Pixels with negative noise at black level 0 are truncated in the source, biasing very dark areas upward.
- Nothing here has been run against real DNGs or a phone; the DNG loader is tested on synthetic DNGs written by tifffile.

## Tests
`pytest` (CPU, synthetic data only; the dummy `framesweeps/` are sampled if present).
