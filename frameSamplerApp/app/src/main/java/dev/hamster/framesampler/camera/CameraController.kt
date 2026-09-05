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
    private var jpegReader: ImageReader? = null
    private var previewSurface: Surface? = null
    private var averager: JpegAverager? = null

    @Volatile
    private var pendingImage: kotlinx.coroutines.CompletableDeferred<ByteArray>? = null

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

    /** Creates the capture session (preview + JPEG outputs) and starts the automatic preview stream. */
    suspend fun startPreview(surface: Surface) {
        val dev = device ?: error("Camera not open")
        val c = caps ?: error("Capabilities not loaded")
        previewSurface = surface

        jpegReader?.close()
        val reader = ImageReader.newInstance(c.largestJpegSize.width, c.largestJpegSize.height, ImageFormat.JPEG, 4)
        reader.setOnImageAvailableListener({ r -> onImageAvailable(r) }, imageHandler)
        jpegReader = reader

        session = createSession(dev, listOf(surface, reader.surface))
        startAutoPreviewRepeating()
    }

    private fun onImageAvailable(reader: ImageReader) {
        val image = try {
            reader.acquireNextImage()
        } catch (e: Exception) {
            Log.w(TAG, "acquireNextImage failed, will wait for next frame", e)
            null
        } ?: return
        try {
            val buffer = image.planes[0].buffer
            val bytes = ByteArray(buffer.remaining())
            buffer.get(bytes)
            val deferred = pendingImage
            if (deferred != null && !deferred.isCompleted) {
                deferred.complete(bytes)
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

    private suspend fun captureJpegFrame(request: CaptureRequest): Pair<TotalCaptureResult, ByteArray> {
        val deferred = CompletableDeferred<ByteArray>()
        pendingImage = deferred
        val result = captureOne(request)
        val bytes = deferred.await()
        pendingImage = null
        return result to bytes
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
        val reader = jpegReader ?: error("JPEG reader not initialized")
        val s = session ?: error("No active session")

        val records = mutableListOf<CaptureRecord>()
        var unsettledCount = 0
        val total = config.totalCaptures

        if (config.framesToAverage > 1) {
            averager = JpegAverager(c.largestJpegSize.width, c.largestJpegSize.height)
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
                        var singleFrameBytes: ByteArray? = null
                        var lastManual: ManualCapture? = null

                        for (frame in 0 until config.framesToAverage) {
                            val manual = buildManualRequest(
                                listOf(reader.surface), iso, exposureNs, focus, forStillCapture = true,
                            )
                            lastManual = manual
                            val (result, bytes) = captureJpegFrame(manual.request)
                            if (firstResult == null) firstResult = result
                            if (avg != null) {
                                avg.add(bytes)
                            } else {
                                singleFrameBytes = bytes
                            }
                        }

                        val outputBytes = avg?.resultJpegBytes() ?: singleFrameBytes!!
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
        jpegReader?.close()
        jpegReader = null
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

    fun shutdown() {
        close()
        cameraThread.quitSafely()
        imageThread.quitSafely()
    }
}
