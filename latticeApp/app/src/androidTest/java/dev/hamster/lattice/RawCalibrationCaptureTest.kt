package dev.hamster.lattice

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.ImageFormat
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraMetadata
import android.hardware.camera2.CaptureFailure
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.DngCreator
import android.hardware.camera2.TotalCaptureResult
import android.hardware.camera2.params.BlackLevelPattern
import android.hardware.camera2.params.ColorSpaceTransform
import android.hardware.camera2.params.LensShadingMap
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.RggbChannelVector
import android.hardware.camera2.params.SessionConfiguration
import android.hardware.camera2.params.TonemapCurve
import android.media.Image
import android.media.ImageReader
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.util.Range
import android.util.Size
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.hamster.lattice.camera.CameraCapabilities
import dev.hamster.lattice.camera.CameraCapabilitiesReader
import dev.hamster.lattice.camera.HandlerExecutor
import dev.hamster.lattice.camera.boxDownscale
import dev.hamster.lattice.camera.encodePixels
import dev.hamster.lattice.camera.yuv420ToArgb
import dev.hamster.lattice.model.OutputFormat
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.fail
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.ByteOrder
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/**
 * Raw + YUV paired capture for the ISO / exposure-time simulator (Python package `camsim`).
 *
 * Not a regression test: it drives the camera and writes DNG / PNG / JSON files for offline
 * calibration. Run it with `scripts/windows/capture.py` (which builds, installs, grants
 * permissions, runs this and pulls the results), or by hand:
 *
 *   adb shell am instrument -w -r -e class dev.hamster.lattice.RawCalibrationCaptureTest \
 *       -e mode scene -e outName 20260911_144103 -e focusD 1.0 \
 *       dev.hamster.lattice.test/androidx.test.runner.AndroidJUnitRunner
 *
 * Output: `<app external files dir>/rawcal/<outName>/` =
 * `/sdcard/Android/data/dev.hamster.lattice/files/rawcal/<outName>/` containing, per capture,
 * `iso0050_exp100000us_fd1p00D_raw_f00.dng|.png|_full.png|.json`, plus `capture_manifest.json`
 * and `characteristics.json`. Progress is reported as `INSTRUMENTATION_STATUS: rawcal=...`.
 *
 * Instrumentation arguments (all optional except where noted):
 *  - mode        scene | dark | flat | pairs | yuvonly   (default scene)
 *  - outName     output sub-folder name (default = session, else mode)
 *  - session     sweep session name (scene mode; recorded in the manifest)
 *  - focusD      requested focus in diopters (default 1.0); scene mode: must be a sweep focus value
 *  - downscale   factor for the sweep-equivalent PNG (default 2, as the sweep)
 *  - isos        comma list of ISO values, overrides the mode default
 *  - exposuresNs comma list of exposure times in ns, overrides the mode default
 *  - frames      frames per setting, overrides the mode default
 *  - burstFrames scene: burst length at ISO 50 / 100 ms (default 16)
 *  - bracketExposuresNs scene: raw bracket exposures at ISO 50 (default 100,25,6.25,1.5625 ms)
 *  - bracketFrames scene: frames per bracket exposure (default 4)
 *  - rampSteps   flat: exposures per ISO in the ramp (default 12)
 *  - fullPng     first | all | none: full-resolution (factor 1) PNG for ISP fitting (default first)
 *  - settleFrames frames discarded before each setting is captured (default 2, as the sweep + extras)
 *  - awbPreroll  auto-preview frames run first so frozen AWB gains converge like in the app (default 45)
 *  - lsm         scene: also record one capture with STATISTICS_LENS_SHADING_MAP_MODE ON (default 1)
 *
 * The manual request mirrors CameraController.buildManualRequest (see the comment on [manualRequest]).
 * Failures throw with an explicit message; nothing is swallowed silently.
 */
@RunWith(AndroidJUnit4::class)
class RawCalibrationCaptureTest {

    private companion object {
        const val TAG = "RawCal"
        const val MS = 1_000_000L
        const val MAX_EXPOSURE_NS = 100 * MS          // the sweep's (and the HAL's usable) ceiling
        const val CAPTURE_TIMEOUT_MS = 20_000L
        const val IMAGE_TIMEOUT_MS = 10_000L
    }

    // ----------------------------------------------------------------------------------------
    // configuration
    // ----------------------------------------------------------------------------------------
    private class Config(a: Bundle) {
        val mode = a.getString("mode", "scene")
        val session: String? = a.getString("session")
        val outName = a.getString("outName") ?: session ?: mode
        val focusD = a.getString("focusD")?.toFloat() ?: 1.0f
        val downscale = a.getString("downscale")?.toInt() ?: 2
        val isos: List<Int>? = list(a.getString("isos")) { it.trim().toInt() }
        val exposuresNs: List<Long>? = list(a.getString("exposuresNs")) { it.trim().toDouble().toLong() }
        val frames: Int? = a.getString("frames")?.toInt()
        val burstFrames = a.getString("burstFrames")?.toInt() ?: 16
        val bracketExposuresNs: List<Long> = list(a.getString("bracketExposuresNs")) { it.trim().toDouble().toLong() }
            ?: listOf(100_000_000L, 25_000_000L, 6_250_000L, 1_562_500L)
        val bracketFrames = a.getString("bracketFrames")?.toInt() ?: 4
        val rampSteps = a.getString("rampSteps")?.toInt() ?: 12
        val fullPng = a.getString("fullPng") ?: "first"
        val settleFrames = a.getString("settleFrames")?.toInt() ?: 2
        val awbPreroll = a.getString("awbPreroll")?.toInt() ?: 45
        val lsm = (a.getString("lsm") ?: "1") != "0"

        private fun <T> list(s: String?, f: (String) -> T): List<T>? =
            s?.split(',')?.filter { it.isNotBlank() }?.map(f)
    }

    /** One group of identical frames. */
    private data class Job(
        val tag: String,
        val iso: Int,
        val exposureNs: Long,
        val frames: Int,
        val raw: Boolean,
        val png: Boolean,
        /** Full-resolution PNG for frames: first | all | none. */
        val full: String,
        val lensShadingMap: Boolean = false,
    )

    // ----------------------------------------------------------------------------------------
    // test entry
    // ----------------------------------------------------------------------------------------
    private lateinit var cfg: Config
    private lateinit var outDir: File
    private val entries = JSONArray()
    private var nFiles = 0

    private val thread = HandlerThread("rawcal").apply { start() }
    private val handler = Handler(thread.looper)
    private val imgThread = HandlerThread("rawcalImages").apply { start() }
    private val imgHandler = Handler(imgThread.looper)

    private val rawImages = LinkedBlockingQueue<Image>()
    private val yuvImages = LinkedBlockingQueue<Image>()

    private var camera: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var rawReader: ImageReader? = null
    private var yuvReader: ImageReader? = null

    @Test
    fun capture() {
        val instr = InstrumentationRegistry.getInstrumentation()
        cfg = Config(InstrumentationRegistry.getArguments())
        val context = instr.targetContext
        if (context.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            fail(
                "CAMERA permission is not granted to ${context.packageName}. Run: " +
                    "adb shell pm grant ${context.packageName} android.permission.CAMERA",
            )
        }
        val manager = context.getSystemService(CameraManager::class.java)
        val cameraId = CameraCapabilitiesReader.selectCameraId(manager)
        val chars = manager.getCameraCharacteristics(cameraId)
        val caps = CameraCapabilitiesReader.read(manager, cameraId)
        if (!caps.supportsManualSensor || !caps.supportsRaw) {
            fail("Camera $cameraId lacks MANUAL_SENSOR or RAW (manual=${caps.supportsManualSensor}, raw=${caps.supportsRaw})")
        }

        outDir = File(context.getExternalFilesDir(null), "rawcal/${cfg.outName}")
        if (outDir.exists() && outDir.listFiles()?.isNotEmpty() == true) {
            fail("Output folder ${outDir.absolutePath} already exists and is not empty; choose another -e outName or delete it")
        }
        outDir.mkdirs()
        status("start mode=${cfg.mode} out=${outDir.absolutePath}")
        File(outDir, "characteristics.json").writeText(dumpCharacteristics(chars).toString(2))

        try {
            val jobs = plan(cfg, caps, chars)
            val needRaw = cfg.mode != "yuvonly"
            openCamera(manager, cameraId)
            buildSession(chars, caps, needRaw)
            if (cfg.mode != "dark") preroll(caps)
            var settingKey = ""
            var done = 0
            for (job in jobs) {
                val key = "${job.iso}/${job.exposureNs}"
                capturesFor(job, chars, caps, key != settingKey)
                settingKey = key
                done++
                status("job $done/${jobs.size} tag=${job.tag} iso=${job.iso} exp=${job.exposureNs}ns files=$nFiles")
            }
            writeManifest(chars, caps, cameraId)
            status("done files=$nFiles out=${outDir.absolutePath}")
            Log.i(TAG, "done: $nFiles files in ${outDir.absolutePath}")
        } catch (e: Throwable) {
            File(outDir, "capture_error.txt").writeText(e.stackTraceToString())
            Log.e(TAG, "capture failed", e)
            fail("RawCalibrationCaptureTest failed: ${e.message} (partial output in ${outDir.absolutePath})")
        } finally {
            teardown()
        }
    }

    // ----------------------------------------------------------------------------------------
    // planning
    // ----------------------------------------------------------------------------------------
    private fun plan(c: Config, caps: CameraCapabilities, chars: CameraCharacteristics): List<Job> {
        val jobs = mutableListOf<Job>()
        when (c.mode) {
            "scene" -> {
                // 1. the "raw frame": burst at ISO 50 / 100 ms; frame 0 is the canonical one.
                jobs += Job("raw", 50, MAX_EXPOSURE_NS, c.burstFrames, raw = true, png = true, full = c.fullPng)
                // 2. raw bracket at ISO 50 (recovers source-clipped highlights).
                for (e in c.bracketExposuresNs) {
                    jobs += Job("brk", 50, e, c.bracketFrames, raw = true, png = false, full = "none")
                }
                // 3. validation sub-grid: raw-domain ground truth + paired PNGs.
                for (iso in c.isos ?: listOf(100, 400, 1600)) {
                    for (e in c.exposuresNs ?: listOf(1 * MS, 10 * MS, 100 * MS)) {
                        jobs += Job("val", iso, e, c.frames ?: 1, raw = true, png = true, full = c.fullPng)
                    }
                }
                // 4. repeated PNGs at a few sweep points: the real-vs-real noise floor.
                for ((iso, e) in listOf(100 to 10 * MS, 400 to 10 * MS, 1600 to 1 * MS)) {
                    jobs += Job("rep", iso, e, 2, raw = false, png = true, full = "none")
                }
                if (c.lsm && lensShadingMapSupported(chars)) {
                    jobs += Job("lsm", 50, MAX_EXPOSURE_NS, 1, raw = true, png = false, full = "none", lensShadingMap = true)
                }
            }
            "dark" -> {
                for (iso in c.isos ?: listOf(50, 100, 200, 400, 800, 1600)) {
                    for (e in c.exposuresNs ?: listOf(1 * MS, 10 * MS, 100 * MS)) {
                        jobs += Job("dark", iso, e, c.frames ?: 4, raw = true, png = false, full = "none")
                    }
                }
            }
            "flat" -> {
                // exposures are decided per ISO at run time (auto ramp) unless given explicitly
                for (iso in c.isos ?: listOf(50, 100, 400, 1600)) {
                    val ramp = c.exposuresNs ?: autoRamp(iso, caps, chars, c.rampSteps)
                    for (e in ramp) jobs += Job("flat", iso, e, c.frames ?: 2, raw = true, png = false, full = "none")
                }
            }
            "pairs" -> {
                val set = mutableListOf<Pair<Int, Long>>()
                set += 50 to MAX_EXPOSURE_NS
                for (iso in c.isos ?: listOf(100, 400, 1600)) {
                    for (e in c.exposuresNs ?: listOf(1 * MS, 3_162_278L, 10 * MS, 31_622_777L, 100 * MS)) set += iso to e
                }
                for ((iso, e) in set) jobs += Job("pair", iso, e, c.frames ?: 1, raw = true, png = true, full = "all")
            }
            "yuvonly" -> {
                val set = if (c.isos != null && c.exposuresNs != null) {
                    c.isos.flatMap { i -> c.exposuresNs.map { e -> i to e } }
                } else {
                    listOf(100 to 10 * MS, 400 to 10 * MS, 1600 to 1 * MS, 100 to 100 * MS)
                }
                for ((iso, e) in set) jobs += Job("yuv", iso, e, c.frames ?: 2, raw = false, png = true, full = "first")
            }
            else -> fail("Unknown mode '${c.mode}'. Use scene | dark | flat | pairs | yuvonly")
        }
        return jobs
    }

    private fun lensShadingMapSupported(chars: CameraCharacteristics): Boolean {
        val modes = chars.get(CameraCharacteristics.STATISTICS_INFO_AVAILABLE_LENS_SHADING_MAP_MODES) ?: return false
        return modes.contains(CameraMetadata.STATISTICS_LENS_SHADING_MAP_MODE_ON)
    }

    /**
     * Flat-field ramp: probe one RAW frame at ISO [iso] to learn the signal rate, then space
     * [steps] exposures log-uniformly from 1/250 of the saturating exposure up to ~90 % of white.
     * Probing needs an open camera, so a private short-lived session is used and closed again.
     */
    private fun autoRamp(iso: Int, caps: CameraCapabilities, chars: CameraCharacteristics, steps: Int): List<Long> {
        val manager = InstrumentationRegistry.getInstrumentation().targetContext.getSystemService(CameraManager::class.java)
        val white = (chars.get(CameraCharacteristics.SENSOR_INFO_WHITE_LEVEL) ?: 1023).toDouble()
        val lower = max(caps.exposureTimeRangeNs.lower, 100_000L)
        val upper = min(caps.exposureTimeRangeNs.upper, MAX_EXPOSURE_NS)
        var probe = (10 * MS).coerceIn(lower, upper)
        var tSat = upper.toDouble()
        openCamera(manager, caps.cameraId)
        try {
            buildSession(chars, caps, withRaw = true)
            for (attempt in 0 until 5) {
                val m = captureOnce(caps, iso, probe, cfg.focusD, wantRaw = true, wantYuv = false, settle = 3)
                val mean = try {
                    meanRaw(m.rawImage!!, chars, m.result)
                } finally {
                    m.close()
                }
                val black = blackLevel(m.result, chars)
                tSat = probe * (0.9 * (white - black)) / max(mean - black, 1.0)
                if (mean < 0.8 * white || probe <= lower) break
                probe = max(lower, probe / 4)      // probe frame was too bright: retry darker
            }
        } finally {
            teardown(keepThreads = true)
        }
        val tMax = min(tSat, upper.toDouble())
        val tMin = max(lower.toDouble(), tMax / 250.0)
        status("flat ISO $iso: saturating exposure ~${(tSat / MS).format(2)} ms, ramp ${(tMin / MS).format(3)}..${(tMax / MS).format(3)} ms")
        return List(steps) { i -> (tMin * (tMax / tMin).pow(i / (steps - 1.0))).toLong() }
    }

    private fun Double.format(n: Int) = String.format(Locale.US, "%.${n}f", this)

    // ----------------------------------------------------------------------------------------
    // Camera2 plumbing
    // ----------------------------------------------------------------------------------------
    private fun openCamera(manager: CameraManager, id: String) {
        val latch = CountDownLatch(1)
        var dev: CameraDevice? = null
        var err: String? = null
        @Suppress("MissingPermission")
        manager.openCamera(id, object : CameraDevice.StateCallback() {
            override fun onOpened(c: CameraDevice) { dev = c; latch.countDown() }
            override fun onDisconnected(c: CameraDevice) { err = "camera disconnected"; c.close(); latch.countDown() }
            override fun onError(c: CameraDevice, e: Int) { err = "camera error $e"; c.close(); latch.countDown() }
        }, handler)
        if (!latch.await(15, TimeUnit.SECONDS)) fail("Timed out opening camera $id")
        camera = dev ?: throw IllegalStateException("Camera $id failed to open: $err")
    }

    /** RAW_SENSOR (optional) + YUV_420_888 outputs, full size, as the paired session. */
    private fun buildSession(chars: CameraCharacteristics, caps: CameraCapabilities, withRaw: Boolean) {
        val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)!!
        val dev = camera ?: error("camera not open")
        yuvReader?.close()
        rawReader?.close()
        rawImages.clear(); yuvImages.clear()
        val yuvSize = caps.largestYuvSize
        yuvReader = ImageReader.newInstance(yuvSize.width, yuvSize.height, ImageFormat.YUV_420_888, 3).also { r ->
            r.setOnImageAvailableListener({ rd -> rd.acquireNextImage()?.let { yuvImages.offer(it) } }, imgHandler)
        }
        val outputs = mutableListOf(OutputConfiguration(yuvReader!!.surface))
        if (withRaw) {
            val rawSize: Size = map.getOutputSizes(ImageFormat.RAW_SENSOR)?.maxByOrNull { it.width.toLong() * it.height }
                ?: throw IllegalStateException("RAW_SENSOR has no output size on this camera")
            rawReader = ImageReader.newInstance(rawSize.width, rawSize.height, ImageFormat.RAW_SENSOR, 3).also { r ->
                r.setOnImageAvailableListener({ rd -> rd.acquireNextImage()?.let { rawImages.offer(it) } }, imgHandler)
            }
            outputs += OutputConfiguration(rawReader!!.surface)
        } else {
            rawReader = null
        }
        val latch = CountDownLatch(1)
        var s: CameraCaptureSession? = null
        dev.createCaptureSession(
            SessionConfiguration(
                SessionConfiguration.SESSION_REGULAR, outputs, HandlerExecutor(handler),
                object : CameraCaptureSession.StateCallback() {
                    override fun onConfigured(cs: CameraCaptureSession) { s = cs; latch.countDown() }
                    override fun onConfigureFailed(cs: CameraCaptureSession) { latch.countDown() }
                },
            ),
        )
        if (!latch.await(15, TimeUnit.SECONDS)) fail("Timed out configuring the capture session")
        session = s ?: throw IllegalStateException("Capture session configuration failed (RAW+YUV streams unsupported together?)")
    }

    /**
     * Lets AWB converge on the scene the way the app's auto preview did before a sweep took over,
     * so that the frozen gains (CONTROL_AWB_MODE_AUTO + AWB_LOCK under CONTROL_MODE_OFF) are the
     * scene's, not the HAL's cold-start values. The gains actually used are in every sidecar.
     */
    private fun preroll(caps: CameraCapabilities) {
        val n = cfg.awbPreroll
        if (n <= 0) return
        val dev = camera!!
        val b = dev.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW)
        b.addTarget(yuvReader!!.surface)
        b.set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
        b.set(CaptureRequest.CONTROL_AE_MODE, CameraMetadata.CONTROL_AE_MODE_ON)
        b.set(CaptureRequest.CONTROL_AF_MODE, CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
        b.set(CaptureRequest.CONTROL_AWB_MODE, CameraMetadata.CONTROL_AWB_MODE_AUTO)
        val latch = CountDownLatch(n)
        session!!.setRepeatingRequest(b.build(), object : CameraCaptureSession.CaptureCallback() {
            override fun onCaptureCompleted(s: CameraCaptureSession, r: CaptureRequest, t: TotalCaptureResult) {
                latch.countDown()
                drain(yuvImages)
            }
        }, handler)
        val ok = latch.await(30, TimeUnit.SECONDS)
        session!!.stopRepeating()
        try { session!!.abortCaptures() } catch (_: Exception) {}
        Thread.sleep(300)
        drain(yuvImages); drain(rawImages)
        if (!ok) Log.w(TAG, "AWB pre-roll did not finish $n frames in 30 s; continuing")
        status("awb preroll done ($n frames)")
    }

    private data class ManualRequest(val request: CaptureRequest, val iso: Int, val exposureNs: Long, val focus: Float)

    /**
     * Same request as the sweep. DUPLICATED from CameraController.buildManualRequest (lines ~250-300,
     * `forStillCapture = true`, `kelvin = null` default path): TEMPLATE_STILL_CAPTURE, CONTROL_MODE_OFF,
     * AE/AF off, AWB_MODE_AUTO + AWB_LOCK (frozen gains), ISO/exposure/focus clamped to the camera's
     * ranges, SENSOR_FRAME_DURATION = clamp(exp + 10 ms), NR/EDGE off with MANUAL_POST_PROCESSING.
     * The controller's version is private and bound to its UI state, so it is not reachable here.
     * Keep both in sync if the controller changes.
     */
    private fun manualRequest(
        caps: CameraCapabilities,
        targets: List<android.view.Surface>,
        iso: Int,
        exposureNs: Long,
        focus: Float,
        lensShadingMap: Boolean = false,
    ): ManualRequest {
        val dev = camera!!
        val clampedIso = iso.coerceIn(caps.sensitivityRange.lower, caps.sensitivityRange.upper)
        val clampedExp = exposureNs.coerceIn(caps.exposureTimeRangeNs.lower, caps.exposureTimeRangeNs.upper)
        val maxFocus = if (caps.minFocusDistanceDiopters <= 0f) 0f else caps.minFocusDistanceDiopters
        val clampedFocus = focus.coerceIn(0f, maxFocus)

        val b = dev.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE)
        targets.forEach { b.addTarget(it) }
        b.set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_OFF)
        b.set(CaptureRequest.CONTROL_AE_MODE, CameraMetadata.CONTROL_AE_MODE_OFF)
        b.set(CaptureRequest.CONTROL_AF_MODE, CameraMetadata.CONTROL_AF_MODE_OFF)
        b.set(CaptureRequest.CONTROL_AWB_MODE, CameraMetadata.CONTROL_AWB_MODE_AUTO)
        b.set(CaptureRequest.CONTROL_AWB_LOCK, true)
        b.set(CaptureRequest.SENSOR_SENSITIVITY, clampedIso)
        b.set(CaptureRequest.SENSOR_EXPOSURE_TIME, clampedExp)
        b.set(CaptureRequest.LENS_FOCUS_DISTANCE, clampedFocus)
        val frameDuration = (clampedExp + 10_000_000L).coerceAtMost(caps.maxFrameDurationNs).coerceAtLeast(clampedExp)
        b.set(CaptureRequest.SENSOR_FRAME_DURATION, frameDuration)
        if (caps.supportsManualPostProcessing) {
            b.set(CaptureRequest.NOISE_REDUCTION_MODE, CameraMetadata.NOISE_REDUCTION_MODE_OFF)
            b.set(CaptureRequest.EDGE_MODE, CameraMetadata.EDGE_MODE_OFF)
        }
        if (lensShadingMap) {
            b.set(CaptureRequest.STATISTICS_LENS_SHADING_MAP_MODE, CameraMetadata.STATISTICS_LENS_SHADING_MAP_MODE_ON)
        }
        return ManualRequest(b.build(), clampedIso, clampedExp, clampedFocus)
    }

    /** Mirrors CameraController.checkSettled. */
    private fun settled(result: TotalCaptureResult, m: ManualRequest): Boolean {
        val actualIso = result.get(CaptureResult.SENSOR_SENSITIVITY)
        val actualExp = result.get(CaptureResult.SENSOR_EXPOSURE_TIME)
        val actualFocus = result.get(CaptureResult.LENS_FOCUS_DISTANCE)
        val lensState = result.get(CaptureResult.LENS_STATE)
        val tol = (m.exposureNs / 200).coerceAtLeast(1_000L)
        return actualIso == m.iso &&
            actualExp != null && abs(actualExp - m.exposureNs) <= tol &&
            actualFocus != null && abs(actualFocus - m.focus) < 0.01f &&
            (lensState == null || lensState == CameraMetadata.LENS_STATE_STATIONARY)
    }

    private class Shot(
        val result: TotalCaptureResult,
        val request: ManualRequest,
        val rawImage: Image?,
        val yuvImage: Image?,
        val settled: Boolean,
    ) {
        fun close() {
            rawImage?.close()
            yuvImage?.close()
        }
    }

    private fun drain(q: LinkedBlockingQueue<Image>) {
        while (true) (q.poll() ?: return).close()
    }

    private fun runCapture(request: CaptureRequest, timeoutMs: Long = CAPTURE_TIMEOUT_MS): TotalCaptureResult {
        val out = LinkedBlockingQueue<Any>()
        session!!.capture(request, object : CameraCaptureSession.CaptureCallback() {
            override fun onCaptureCompleted(s: CameraCaptureSession, r: CaptureRequest, t: TotalCaptureResult) {
                out.offer(t)
            }

            override fun onCaptureFailed(s: CameraCaptureSession, r: CaptureRequest, f: CaptureFailure) {
                out.offer(IllegalStateException("capture failed, reason=${f.reason}"))
            }
        }, handler)
        val got = out.poll(timeoutMs, TimeUnit.MILLISECONDS) ?: throw IllegalStateException("capture timed out after $timeoutMs ms")
        if (got is Throwable) throw got
        return got as TotalCaptureResult
    }

    private fun takeImage(q: LinkedBlockingQueue<Image>, timestamp: Long, what: String): Image {
        val deadline = System.nanoTime() + IMAGE_TIMEOUT_MS * 1_000_000
        while (System.nanoTime() < deadline) {
            val img = q.poll(200, TimeUnit.MILLISECONDS) ?: continue
            if (img.timestamp == timestamp) return img
            img.close()    // stale frame from a settle capture
        }
        throw IllegalStateException("no $what image with timestamp $timestamp arrived within $IMAGE_TIMEOUT_MS ms")
    }

    /**
     * Settles (discarding frames) then captures once into the requested streams. Images are
     * matched to the TotalCaptureResult by timestamp so DngCreator gets the right pair.
     */
    private fun captureOnce(
        caps: CameraCapabilities,
        iso: Int,
        exposureNs: Long,
        focus: Float,
        wantRaw: Boolean,
        wantYuv: Boolean,
        settle: Int,
        lensShadingMap: Boolean = false,
    ): Shot {
        val targets = mutableListOf<android.view.Surface>()
        // Always target at least YUV so the HAL pipeline matches the sweep's YUV capture.
        if (wantRaw) targets += rawReader!!.surface
        if (wantYuv || !wantRaw) targets += yuvReader!!.surface
        val m = manualRequest(caps, targets, iso, exposureNs, focus, lensShadingMap)
        val timeout = CAPTURE_TIMEOUT_MS + 4 * m.exposureNs / 1_000_000
        var ok = false
        var extra = 0
        repeat(settle) { runCapture(m.request, timeout); drain(rawImages); drain(yuvImages) }
        // Like the app: keep going (up to 8 frames) until the request has visibly taken effect.
        var result = runCapture(m.request, timeout)
        ok = settled(result, m)
        while (!ok && extra < 8) {
            drain(rawImages); drain(yuvImages)
            result = runCapture(m.request, timeout)
            ok = settled(result, m)
            extra++
        }
        val ts = result.get(CaptureResult.SENSOR_TIMESTAMP) ?: throw IllegalStateException("result without SENSOR_TIMESTAMP")
        val raw = if (wantRaw) takeImage(rawImages, ts, "RAW") else null
        val yuv = if (wantYuv || !wantRaw) takeImage(yuvImages, ts, "YUV") else null
        return Shot(result, m, raw, yuv, ok)
    }

    // ----------------------------------------------------------------------------------------
    // per-job capture + writing
    // ----------------------------------------------------------------------------------------
    private fun capturesFor(job: Job, chars: CameraCharacteristics, caps: CameraCapabilities, firstOfSetting: Boolean) {
        for (frame in 0 until job.frames) {
            val shot = captureOnce(
                caps, job.iso, job.exposureNs, cfg.focusD,
                wantRaw = job.raw, wantYuv = job.png,
                settle = if (frame == 0 && firstOfSetting) cfg.settleFrames else 0,
                lensShadingMap = job.lensShadingMap,
            )
            try {
                writeShot(job, frame, shot, chars, caps)
            } finally {
                shot.close()
            }
        }
    }

    private fun stem(job: Job, frame: Int): String {
        val iso = job.iso.toString().padStart(4, '0')
        val us = (job.exposureNs / 1000).toString().padStart(6, '0')
        val fd = String.format(Locale.US, "%.2f", cfg.focusD).replace('.', 'p')
        // Same convention as SweepStorage.filenameFor, with the capture tag in place of avg/ds/index.
        return "iso${iso}_exp${us}us_fd${fd}D_${job.tag}_f${frame.toString().padStart(2, '0')}"
    }

    private fun writeShot(job: Job, frame: Int, shot: Shot, chars: CameraCharacteristics, caps: CameraCapabilities) {
        val stem = stem(job, frame)
        val e = JSONObject()
        e.put("tag", job.tag)
        e.put("frame", frame)
        e.put("iso", shot.result.get(CaptureResult.SENSOR_SENSITIVITY) ?: JSONObject.NULL)
        e.put("requestedIso", shot.request.iso)
        e.put("exposureNs", shot.result.get(CaptureResult.SENSOR_EXPOSURE_TIME) ?: JSONObject.NULL)
        e.put("requestedExposureNs", shot.request.exposureNs)
        e.put("focusD", (shot.result.get(CaptureResult.LENS_FOCUS_DISTANCE) ?: shot.request.focus).toDouble())
        e.put("requestedFocusD", cfg.focusD.toDouble())
        e.put("settled", shot.settled)

        val side = JSONObject()
        side.put("tag", job.tag)
        side.put("frame", frame)
        side.put("settled", shot.settled)
        side.put("request", JSONObject().apply {
            put("iso", shot.request.iso)
            put("exposureNs", shot.request.exposureNs)
            put("focusD", shot.request.focus.toDouble())
        })

        if (shot.rawImage != null) {
            val dng = "$stem.dng"
            File(outDir, dng).outputStream().use { os ->
                DngCreator(chars, shot.result).use { it.writeImage(os, shot.rawImage) }
            }
            nFiles++
            e.put("file", dng)
            side.put("file", dng)
            side.put("raw", JSONObject().apply {
                put("width", shot.rawImage.width)
                put("height", shot.rawImage.height)
                put("cfa", cfaString(chars))
                put("blackLevelPattern", chars.get(CameraCharacteristics.SENSOR_BLACK_LEVEL_PATTERN)?.let { toJson(it) } ?: JSONObject.NULL)
                put("whiteLevel", chars.get(CameraCharacteristics.SENSOR_INFO_WHITE_LEVEL) ?: JSONObject.NULL)
            })
        } else {
            e.put("file", "$stem.png")      // PNG-only entry (rep / yuv): the file field names the PNG
            side.put("file", "$stem.png")
        }
        if (shot.yuvImage != null && job.png) {
            val img = shot.yuvImage
            val px = yuv420ToArgb(img)               // the app's exact integer BT.601 converter
            val scaled = boxDownscale(px, img.width, img.height, cfg.downscale)   // the app's exact box downscale
            val png = "$stem.png"
            File(outDir, png).writeBytes(encodePixels(scaled.pixels, scaled.width, scaled.height, OutputFormat.PNG))
            nFiles++
            e.put("png", png)
            e.put("downscale", cfg.downscale)
            side.put("png", png)
            side.put("downscale", cfg.downscale)
            if (job.full == "all" || (job.full == "first" && frame == 0)) {
                val full = "${stem}_full.png"
                File(outDir, full).writeBytes(encodePixels(px, img.width, img.height, OutputFormat.PNG))
                nFiles++
                e.put("pngFull", full)
                side.put("pngFull", full)
            }
        }
        side.put("result", resultToJson(shot.result))
        val js = "$stem.json"
        File(outDir, js).writeText(side.toString(1))
        e.put("json", js)
        nFiles++
        entries.put(e)
    }

    private fun writeManifest(chars: CameraCharacteristics, caps: CameraCapabilities, cameraId: String) {
        val m = JSONObject()
        m.put("mode", cfg.mode)
        m.put("session", cfg.session ?: JSONObject.NULL)
        m.put("cameraId", cameraId)
        m.put("device", JSONObject().apply {
            put("manufacturer", Build.MANUFACTURER)
            put("model", Build.MODEL)
            put("fingerprint", Build.FINGERPRINT)
            put("sdk", Build.VERSION.SDK_INT)
        })
        m.put("focusD", cfg.focusD.toDouble())
        m.put("downscale", cfg.downscale)
        m.put("createdMs", System.currentTimeMillis())
        m.put("note", when (cfg.mode) {
            "dark" -> "lens must be covered"
            "flat" -> "uniform diffuse target"
            else -> "tripod untouched since the sweep"
        })
        m.put("settings", JSONObject().apply {
            put("settleFrames", cfg.settleFrames)
            put("awbPreroll", cfg.awbPreroll)
            put("fullPng", cfg.fullPng)
            put("yuvSize", "${caps.largestYuvSize.width}x${caps.largestYuvSize.height}")
            put("exposureRangeNs", JSONArray(listOf(caps.exposureTimeRangeNs.lower, caps.exposureTimeRangeNs.upper)))
            put("sensitivityRange", JSONArray(listOf(caps.sensitivityRange.lower, caps.sensitivityRange.upper)))
        })
        m.put("entries", entries)
        File(outDir, "capture_manifest.json").writeText(m.toString(2))
        nFiles++
    }

    private fun teardown(keepThreads: Boolean = false) {
        try { session?.close() } catch (_: Exception) {}
        session = null
        try { camera?.close() } catch (_: Exception) {}
        camera = null
        drain(rawImages); drain(yuvImages)
        rawReader?.close(); rawReader = null
        yuvReader?.close(); yuvReader = null
        if (!keepThreads) {
            thread.quitSafely()
            imgThread.quitSafely()
        }
    }

    private fun status(msg: String) {
        Log.i(TAG, msg)
        InstrumentationRegistry.getInstrumentation().sendStatus(0, Bundle().apply { putString("rawcal", msg) })
    }

    // ----------------------------------------------------------------------------------------
    // raw statistics (flat-field exposure ramp)
    // ----------------------------------------------------------------------------------------
    private fun blackLevel(result: TotalCaptureResult, chars: CameraCharacteristics): Double {
        result.get(CaptureResult.SENSOR_DYNAMIC_BLACK_LEVEL)?.let { return it.average() }
        val p = chars.get(CameraCharacteristics.SENSOR_BLACK_LEVEL_PATTERN) ?: return 0.0
        val a = IntArray(4)
        p.copyTo(a, 0)
        return a.average()
    }

    /** Mean raw DN of a 256x256 centre block (RAW16 container, 16-bit little endian samples). */
    private fun meanRaw(img: Image, chars: CameraCharacteristics, result: TotalCaptureResult): Double {
        val plane = img.planes[0]
        val buf = plane.buffer.order(ByteOrder.nativeOrder())
        val y0 = img.height / 2 - 128
        val x0 = img.width / 2 - 128
        var sum = 0.0
        var n = 0
        for (y in 0 until 256) {
            for (x in 0 until 256) {
                val idx = (y0 + y) * plane.rowStride + (x0 + x) * plane.pixelStride
                sum += (buf.getShort(idx).toInt() and 0xFFFF)
                n++
            }
        }
        return sum / n
    }

    // ----------------------------------------------------------------------------------------
    // JSON dumps
    // ----------------------------------------------------------------------------------------
    private fun cfaString(chars: CameraCharacteristics): String =
        when (chars.get(CameraCharacteristics.SENSOR_INFO_COLOR_FILTER_ARRANGEMENT)) {
            CameraMetadata.SENSOR_INFO_COLOR_FILTER_ARRANGEMENT_RGGB -> "RGGB"
            CameraMetadata.SENSOR_INFO_COLOR_FILTER_ARRANGEMENT_GRBG -> "GRBG"
            CameraMetadata.SENSOR_INFO_COLOR_FILTER_ARRANGEMENT_GBRG -> "GBRG"
            CameraMetadata.SENSOR_INFO_COLOR_FILTER_ARRANGEMENT_BGGR -> "BGGR"
            else -> "UNKNOWN"
        }

    private fun resultToJson(result: TotalCaptureResult): JSONObject {
        val o = JSONObject()
        for (key in result.keys) {
            try {
                o.put(key.name, toJson(result.get(key)))
            } catch (e: Exception) {
                o.put(key.name, "<unreadable: ${e.message}>")
            }
        }
        return o
    }

    private fun dumpCharacteristics(chars: CameraCharacteristics): JSONObject {
        val o = JSONObject()
        for (key in chars.keys) {
            try {
                val v = chars.get(key)
                o.put(key.name, if (v?.javaClass?.name?.contains("StreamConfigurationMap") == true) "<omitted>" else toJson(v))
            } catch (e: Exception) {
                o.put(key.name, "<unreadable: ${e.message}>")
            }
        }
        o.put("build.model", Build.MODEL)
        return o
    }

    /** Best-effort conversion of Camera2 value types to JSON. Unknown types fall back to toString(). */
    private fun toJson(v: Any?): Any = when (v) {
        null -> JSONObject.NULL
        is Number, is Boolean, is String -> v
        is IntArray -> JSONArray(v.toList())
        is LongArray -> JSONArray(v.toList())
        is FloatArray -> JSONArray(v.map { it.toDouble() })
        is DoubleArray -> JSONArray(v.toList())
        is BooleanArray -> JSONArray(v.toList())
        is ByteArray -> JSONArray(v.map { it.toInt() })
        is Array<*> -> JSONArray().also { a -> v.forEach { a.put(toJson(it)) } }
        is android.util.Pair<*, *> -> JSONArray().put(toJson(v.first)).put(toJson(v.second))
        is Range<*> -> JSONArray().put(toJson(v.lower)).put(toJson(v.upper))
        is Size -> "${v.width}x${v.height}"
        is android.graphics.Rect -> JSONArray(listOf(v.left, v.top, v.right, v.bottom))
        is RggbChannelVector -> JSONArray(listOf(v.red, v.greenEven, v.greenOdd, v.blue).map { it.toDouble() })
        is ColorSpaceTransform -> {
            val r = arrayOfNulls<android.util.Rational>(9)
            v.copyElements(r, 0)
            JSONArray(r.map { it!!.numerator.toDouble() / it.denominator })
        }
        is BlackLevelPattern -> {
            val a = IntArray(4)
            v.copyTo(a, 0)
            JSONArray(a.toList())
        }
        is LensShadingMap -> JSONObject().apply {
            put("rows", v.rowCount)
            put("columns", v.columnCount)
            val g = FloatArray(v.gainFactorCount)
            v.copyGainFactors(g, 0)
            put("gains", JSONArray(g.map { it.toDouble() }))      // row-major, (R, Geven, Godd, B) per cell
        }
        is TonemapCurve -> JSONObject().apply {
            val chans = listOf("red" to TonemapCurve.CHANNEL_RED, "green" to TonemapCurve.CHANNEL_GREEN, "blue" to TonemapCurve.CHANNEL_BLUE)
            for ((name, ch) in chans) {
                val n = v.getPointCount(ch)
                val pts = FloatArray(n * 2)
                v.copyColorCurve(ch, pts, 0)
                put(name, JSONArray(pts.map { it.toDouble() }))   // interleaved (in, out)
            }
        }
        else -> v.toString()
    }
}
