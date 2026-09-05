package dev.hamster.framesampler.camera

import android.graphics.ImageFormat
import android.hardware.camera2.CameraAccessException
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraMetadata
import android.hardware.camera2.CaptureFailure
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import dev.hamster.framesampler.model.OutputFormat
import dev.hamster.framesampler.model.SweepConfig
import dev.hamster.framesampler.storage.CaptureRecord
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.math.abs

private const val TAG = "CameraController"

data class SweepProgress(
    val done: Int,
    val total: Int,
    val currentIso: Int,
    val currentExposureNs: Long,
    val currentFocus: Float,
    val unsettledCount: Int,
)

/** One frame off the camera: either an encoded JPEG, or decoded pixels from an uncompressed capture. */
sealed interface CapturedFrame {
    class Jpeg(val bytes: ByteArray) : CapturedFrame
    class Pixels(val pixels: IntArray, val width: Int, val height: Int) : CapturedFrame
}

class CameraOpenException(message: String, cause: Throwable? = null) : Exception(message, cause)

/**
 * Single owner of all Camera2 state: device, session, preview/manual requests and the sweep loop.
 * All camera callbacks run on a dedicated background thread; callers drive this via suspend
 * functions from a coroutine (e.g. viewModelScope).
 */
class CameraController(private val cameraManager: CameraManager) {

    private val cameraThread = HandlerThread("camera").apply { start() }
    private val cameraHandler = Handler(cameraThread.looper)
    private val imageThread = HandlerThread("imageReader").apply { start() }
    private val imageHandler = Handler(imageThread.looper)

    var caps: CameraCapabilities? = null
        private set

    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var stillReader: ImageReader? = null
    private var readerFormat: Int = ImageFormat.JPEG
    private var previewSurface: Surface? = null
    private var averager: FrameAverager? = null

    @Volatile
    private var pendingImage: kotlinx.coroutines.CompletableDeferred<CapturedFrame>? = null

    suspend fun open(cameraId: String): CameraCapabilities {
        val resolvedCaps = CameraCapabilitiesReader.read(cameraManager, cameraId)
        caps = resolvedCaps
        device = openDevice(cameraId)
        return resolvedCaps
    }

    @Suppress("MissingPermission")
    private suspend fun openDevice(id: String): CameraDevice = suspendCancellableCoroutine { cont ->
        try {
            cameraManager.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(cd: CameraDevice) {
                    if (cont.isActive) cont.resume(cd)
                }

                override fun onDisconnected(cd: CameraDevice) {
                    cd.close()
                    if (cont.isActive) cont.resumeWithException(CameraOpenException("Camera $id disconnected"))
                }

                override fun onError(cd: CameraDevice, error: Int) {
                    cd.close()
                    if (cont.isActive) cont.resumeWithException(CameraOpenException("Camera $id error code $error"))
                }
            }, cameraHandler)
        } catch (e: CameraAccessException) {
            cont.resumeWithException(CameraOpenException("Cannot access camera $id", e))
        }
    }

    /** Creates the capture session (preview + still output) and starts the automatic preview stream. */
    suspend fun startPreview(surface: Surface) {
        previewSurface = surface
        buildSession(readerFormat)
        startAutoPreviewRepeating()
    }

    /**
     * The still ImageReader's format is fixed when the capture session is configured, so switching
     * output format means tearing the session down and building a new one. Called before a sweep.
     */
    suspend fun ensureOutputFormat(format: OutputFormat) {
        if (format.readerFormat == readerFormat && session != null && stillReader != null) return
        Log.i(TAG, "Rebuilding capture session for ${format.label} (reader format ${format.readerFormat})")
        try {
            session?.close()
        } catch (e: Exception) {
            Log.w(TAG, "closing session before format switch failed", e)
        }
        session = null
        buildSession(format.readerFormat)
    }

    private suspend fun buildSession(format: Int) {
        val dev = device ?: error("Camera not open")
        val c = caps ?: error("Capabilities not loaded")
        val surface = previewSurface ?: error("Preview surface not set")

        stillReader?.close()
        val size = if (format == ImageFormat.JPEG) c.largestJpegSize else c.largestYuvSize
        // YUV frames are far larger in memory than encoded JPEGs, so keep fewer in flight.
        val maxImages = if (format == ImageFormat.JPEG) 4 else 2
        val reader = ImageReader.newInstance(size.width, size.height, format, maxImages)
        reader.setOnImageAvailableListener({ r -> onImageAvailable(r) }, imageHandler)
        stillReader = reader
        readerFormat = format

        session = createSession(dev, listOf(surface, reader.surface))
    }

    private fun onImageAvailable(reader: ImageReader) {
        val image = try {
            reader.acquireNextImage()
        } catch (e: Exception) {
            Log.w(TAG, "acquireNextImage failed, will wait for next frame", e)
            null
        } ?: return
        try {
            val frame = if (image.format == ImageFormat.JPEG) {
                val buffer = image.planes[0].buffer
                val bytes = ByteArray(buffer.remaining())
                buffer.get(bytes)
                CapturedFrame.Jpeg(bytes)
            } else {
                CapturedFrame.Pixels(yuv420ToArgb(image), image.width, image.height)
            }
            val deferred = pendingImage
            if (deferred != null && !deferred.isCompleted) {
                deferred.complete(frame)
            }
        } finally {
            image.close()
        }
    }

    private suspend fun createSession(dev: CameraDevice, surfaces: List<Surface>): CameraCaptureSession =
        suspendCancellableCoroutine { cont ->
            val outputs = surfaces.map { OutputConfiguration(it) }
            val config = SessionConfiguration(
                SessionConfiguration.SESSION_REGULAR,
                outputs,
                HandlerExecutor(cameraHandler),
                object : CameraCaptureSession.StateCallback() {
                    override fun onConfigured(s: CameraCaptureSession) {
                        if (cont.isActive) cont.resume(s)
                    }

                    override fun onConfigureFailed(s: CameraCaptureSession) {
                        if (cont.isActive) cont.resumeWithException(CameraOpenException("Session configuration failed"))
                    }
                },
            )
            dev.createCaptureSession(config)
        }

    private fun buildAutoPreviewRequest(): CaptureRequest {
        val dev = device ?: error("Camera not open")
        val surface = previewSurface ?: error("Preview surface not set")
        val b = dev.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW)
        b.addTarget(surface)
        b.set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
        b.set(CaptureRequest.CONTROL_AE_MODE, CameraMetadata.CONTROL_AE_MODE_ON)
        b.set(CaptureRequest.CONTROL_AF_MODE, CameraMetadata.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
        b.set(CaptureRequest.CONTROL_AWB_MODE, CameraMetadata.CONTROL_AWB_MODE_AUTO)
        return b.build()
    }

    fun startAutoPreviewRepeating() {
        val s = session ?: return
        try {
            s.setRepeatingRequest(buildAutoPreviewRequest(), null, cameraHandler)
        } catch (e: CameraAccessException) {
            Log.e(TAG, "Failed to restart preview repeating request", e)
        } catch (e: IllegalStateException) {
            Log.w(TAG, "Session already closed while restarting preview", e)
        }
    }

    private data class ManualCapture(
        val request: CaptureRequest,
        val clampedIso: Int,
        val clampedExposureNs: Long,
        val clampedFocus: Float,
    )

    private fun buildManualRequest(
        targets: List<Surface>,
        isoValue: Int,
        exposureNs: Long,
        focusDiopters: Float,
        forStillCapture: Boolean,
    ): ManualCapture {
        val c = caps ?: error("Capabilities not loaded")
        val dev = device ?: error("Camera not open")

        val clampedIso = isoValue.coerceIn(c.sensitivityRange.lower, c.sensitivityRange.upper)
        val clampedExp = exposureNs.coerceIn(c.exposureTimeRangeNs.lower, c.exposureTimeRangeNs.upper)
        val maxFocus = if (c.minFocusDistanceDiopters <= 0f) 0f else c.minFocusDistanceDiopters
        val clampedFocus = focusDiopters.coerceIn(0f, maxFocus)

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
        val frameDuration = (clampedExp + 10_000_000L).coerceAtMost(c.maxFrameDurationNs).coerceAtLeast(clampedExp)
        b.set(CaptureRequest.SENSOR_FRAME_DURATION, frameDuration)
        if (c.supportsManualPostProcessing) {
            b.set(CaptureRequest.NOISE_REDUCTION_MODE, CameraMetadata.NOISE_REDUCTION_MODE_OFF)
            b.set(CaptureRequest.EDGE_MODE, CameraMetadata.EDGE_MODE_OFF)
        }
        if (forStillCapture) {
            b.set(CaptureRequest.JPEG_QUALITY, 100.toByte())
        }
        return ManualCapture(b.build(), clampedIso, clampedExp, clampedFocus)
    }

    private suspend fun captureOne(request: CaptureRequest): TotalCaptureResult = suspendCancellableCoroutine { cont ->
        val s = session ?: run {
            cont.resumeWithException(IllegalStateException("No active session"))
            return@suspendCancellableCoroutine
        }
        try {
            s.capture(request, object : CameraCaptureSession.CaptureCallback() {
                override fun onCaptureCompleted(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    result: TotalCaptureResult,
                ) {
                    if (cont.isActive) cont.resume(result)
                }

                override fun onCaptureFailed(
                    session: CameraCaptureSession,
                    request: CaptureRequest,
                    failure: CaptureFailure,
                ) {
                    if (cont.isActive) {
                        cont.resumeWithException(CameraOpenException("Capture failed, reason=${failure.reason}"))
                    }
                }
            }, cameraHandler)
        } catch (e: CameraAccessException) {
            cont.resumeWithException(e)
        }
    }

    private suspend fun captureFrame(request: CaptureRequest): Pair<TotalCaptureResult, CapturedFrame> {
        val deferred = CompletableDeferred<CapturedFrame>()
        pendingImage = deferred
        val result = captureOne(request)
        val frame = deferred.await()
        pendingImage = null
        return result to frame
    }

    private fun checkSettled(result: TotalCaptureResult, iso: Int, exposureNs: Long, focus: Float): Boolean {
        val actualIso = result.get(CaptureResult.SENSOR_SENSITIVITY)
        val actualExp = result.get(CaptureResult.SENSOR_EXPOSURE_TIME)
        val actualFocus = result.get(CaptureResult.LENS_FOCUS_DISTANCE)
        val lensState = result.get(CaptureResult.LENS_STATE)
        // Sensors quantize exposure time to a line-time step, so it rarely lands on the exact
        // requested nanosecond value even once fully settled — allow a small relative tolerance
        // (with an absolute floor for very short exposures) instead of exact equality.
        val expTolerance = (exposureNs / 200).coerceAtLeast(1_000L)
        return actualIso == iso &&
            actualExp != null && abs(actualExp - exposureNs) <= expTolerance &&
            actualFocus != null && abs(actualFocus - focus) < 0.01f &&
            (lensState == null || lensState == CameraMetadata.LENS_STATE_STATIONARY)
    }

    private suspend fun settle(iso: Int, exposureNs: Long, focus: Float, settleFrames: Int): Boolean {
        if (settleFrames <= 0) return true
        val surface = previewSurface ?: error("Preview surface not set")
        val manual = buildManualRequest(listOf(surface), iso, exposureNs, focus, forStillCapture = false)

        var settled = false
        repeat(settleFrames) {
            val result = captureOne(manual.request)
            settled = checkSettled(result, manual.clampedIso, manual.clampedExposureNs, manual.clampedFocus)
        }
        var extra = 0
        while (!settled && extra < 8) {
            val result = captureOne(manual.request)
            settled = checkSettled(result, manual.clampedIso, manual.clampedExposureNs, manual.clampedFocus)
            extra++
        }
        return settled
    }

    /**
     * Runs the full ISO x exposure x focus sweep. [onCaptureWritten] is invoked once per output
     * image (after n-frame averaging) with the record and the encoded JPEG bytes to persist.
     * Restores the automatic preview stream when finished, cancelled, or on error.
     */
    suspend fun runSweep(
        config: SweepConfig,
        onProgress: suspend (SweepProgress) -> Unit,
        onCaptureWritten: suspend (CaptureRecord, ByteArray) -> Unit,
    ): List<CaptureRecord> {
        val c = caps ?: error("Capabilities not loaded")
        // Switching output format rebuilds the session, so do it before grabbing references.
        ensureOutputFormat(config.outputFormat)
        val reader = stillReader ?: error("Still reader not initialized")
        val s = session ?: error("No active session")

        val records = mutableListOf<CaptureRecord>()
        var unsettledCount = 0
        val total = config.totalCaptures

        val frameSize = if (config.outputFormat == OutputFormat.JPEG) c.largestJpegSize else c.largestYuvSize
        if (config.framesToAverage > 1) {
            averager = FrameAverager(frameSize.width, frameSize.height)
        }

        try {
            s.stopRepeating()
            try {
                s.abortCaptures()
            } catch (e: CameraAccessException) {
                Log.w(TAG, "abortCaptures failed", e)
            }

            var index = 0
            for (focus in config.focusValues) {
                for (iso in config.isoValues) {
                    for (exposureNs in config.exposureValuesNs) {
                        currentCoroutineContext().ensureActive()

                        val settled = settle(iso, exposureNs, focus, config.settleFrames)
                        if (!settled) unsettledCount++

                        val avg = averager
                        avg?.reset()
                        var firstResult: TotalCaptureResult? = null
                        var singleFrame: CapturedFrame? = null
                        var lastManual: ManualCapture? = null

                        for (frame in 0 until config.framesToAverage) {
                            val manual = buildManualRequest(
                                listOf(reader.surface), iso, exposureNs, focus, forStillCapture = true,
                            )
                            lastManual = manual
                            val (result, captured) = captureFrame(manual.request)
                            if (firstResult == null) firstResult = result
                            if (avg != null) {
                                avg.add(pixelsOf(captured))
                            } else {
                                singleFrame = captured
                            }
                        }

                        val outputBytes = encodeOutput(
                            avg = avg,
                            singleFrame = singleFrame,
                            fullSize = frameSize,
                            format = config.outputFormat,
                            downscale = config.downscale,
                        )
                        val manual = lastManual!!
                        val result = firstResult!!
                        val record = CaptureRecord(
                            index = index,
                            requestedIso = iso,
                            actualIso = result.get(CaptureResult.SENSOR_SENSITIVITY),
                            requestedExposureNs = exposureNs,
                            actualExposureNs = result.get(CaptureResult.SENSOR_EXPOSURE_TIME),
                            requestedFocusDiopters = focus,
                            actualFocusDiopters = result.get(CaptureResult.LENS_FOCUS_DISTANCE),
                            framesAveraged = config.framesToAverage,
                            extension = config.outputFormat.extension,
                            downscale = config.downscale,
                            outputWidth = downscaledSize(frameSize.width, frameSize.height, config.downscale).first,
                            outputHeight = downscaledSize(frameSize.width, frameSize.height, config.downscale).second,
                            settled = settled,
                            timestampNs = result.get(CaptureResult.SENSOR_TIMESTAMP) ?: System.nanoTime(),
                        )
                        onCaptureWritten(record, outputBytes)
                        records += record
                        index++
                        onProgress(SweepProgress(index, total, manual.clampedIso, manual.clampedExposureNs, manual.clampedFocus, unsettledCount))
                    }
                }
            }
        } finally {
            averager = null
            startAutoPreviewRepeating()
        }
        return records
    }

    fun stopSessionForTeardown() {
        try {
            session?.stopRepeating()
        } catch (e: Exception) {
            Log.w(TAG, "stopRepeating during teardown failed", e)
        }
    }

    fun close() {
        try {
            session?.close()
        } catch (e: Exception) {
            Log.w(TAG, "session close failed", e)
        }
        session = null
        stillReader?.close()
        stillReader = null
        try {
            device?.close()
        } catch (e: Exception) {
            Log.w(TAG, "device close failed", e)
        }
        device = null
        caps = null
        previewSurface = null
        pendingImage = null
    }

    /**
     * Produces the bytes written to disk: average if asked, downscale if asked, then encode.
     *
     * The one path that avoids decoding entirely is a single JPEG frame destined for a JPEG file
     * at full resolution — re-encoding that would only add a generation of compression loss.
     * Downscaling necessarily gives that up, since the pixels have to be touched.
     */
    private fun encodeOutput(
        avg: FrameAverager?,
        singleFrame: CapturedFrame?,
        fullSize: android.util.Size,
        format: OutputFormat,
        downscale: Int,
    ): ByteArray {
        if (avg != null) {
            val averaged = boxDownscale(avg.averagedPixels(), fullSize.width, fullSize.height, downscale)
            return encodePixels(averaged.pixels, averaged.width, averaged.height, format)
        }
        val frame = singleFrame!!
        if (downscale <= 1) return encodeSingle(frame, format)
        val full = framePixels(frame)
        val scaled = boxDownscale(full.pixels, full.width, full.height, downscale)
        return encodePixels(scaled.pixels, scaled.width, scaled.height, format)
    }

    /** A captured frame as pixels, decoding a JPEG only when that is the form it arrived in. */
    private fun framePixels(frame: CapturedFrame): PixelFrame = when (frame) {
        is CapturedFrame.Pixels -> PixelFrame(frame.pixels, frame.width, frame.height)
        is CapturedFrame.Jpeg -> {
            val bmp = android.graphics.BitmapFactory.decodeByteArray(frame.bytes, 0, frame.bytes.size)
                ?: error("Failed to decode captured JPEG")
            try {
                val px = IntArray(bmp.width * bmp.height)
                bmp.getPixels(px, 0, bmp.width, 0, 0, bmp.width, bmp.height)
                PixelFrame(px, bmp.width, bmp.height)
            } finally {
                bmp.recycle()
            }
        }
    }

    /** Decodes to ARGB pixels only when needed — averaging always works in pixel space. */
    private fun pixelsOf(frame: CapturedFrame): IntArray = when (frame) {
        is CapturedFrame.Pixels -> frame.pixels
        is CapturedFrame.Jpeg -> {
            val bmp = android.graphics.BitmapFactory.decodeByteArray(frame.bytes, 0, frame.bytes.size)
                ?: error("Failed to decode captured JPEG for averaging")
            try {
                IntArray(bmp.width * bmp.height).also {
                    bmp.getPixels(it, 0, bmp.width, 0, 0, bmp.width, bmp.height)
                }
            } finally {
                bmp.recycle()
            }
        }
    }

    /**
     * A single JPEG frame destined for a JPEG file is written through untouched — decoding and
     * re-encoding it would only add a generation of compression loss.
     */
    private fun encodeSingle(frame: CapturedFrame, format: OutputFormat): ByteArray = when {
        frame is CapturedFrame.Jpeg && format == OutputFormat.JPEG -> frame.bytes
        frame is CapturedFrame.Pixels -> encodePixels(frame.pixels, frame.width, frame.height, format)
        frame is CapturedFrame.Jpeg -> {
            val bmp = android.graphics.BitmapFactory.decodeByteArray(frame.bytes, 0, frame.bytes.size)
                ?: error("Failed to decode captured JPEG")
            try {
                val px = IntArray(bmp.width * bmp.height)
                bmp.getPixels(px, 0, bmp.width, 0, 0, bmp.width, bmp.height)
                encodePixels(px, bmp.width, bmp.height, format)
            } finally {
                bmp.recycle()
            }
        }
        else -> error("Unreachable")
    }

    fun shutdown() {
        close()
        cameraThread.quitSafely()
        imageThread.quitSafely()
    }
}
