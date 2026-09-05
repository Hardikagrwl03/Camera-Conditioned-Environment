package dev.hamster.framesampler.camera

import android.graphics.ImageFormat
import android.graphics.Rect
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.hardware.camera2.params.StreamConfigurationMap
import android.util.Log
import android.util.Range
import android.util.Size
import android.view.SurfaceHolder

data class CameraCapabilities(
    val cameraId: String,
    val sensitivityRange: Range<Int>,
    val exposureTimeRangeNs: Range<Long>,
    val minFocusDistanceDiopters: Float,
    val hyperfocalDistanceDiopters: Float,
    val maxFrameDurationNs: Long,
    val supportsManualSensor: Boolean,
    val supportsManualPostProcessing: Boolean,
    val supportsRaw: Boolean,
    val hardwareLevel: Int,
    val activeArraySize: Rect,
    val largestJpegSize: Size,
    val largestYuvSize: Size,
    val previewSize: Size,
    val sensorOrientation: Int,
)

/**
 * Aspect ratio (width / height) the preview occupies **on screen**, as opposed to in sensor
 * coordinates. Camera2 reports sizes in sensor coordinates, which are landscape on a sensor
 * mounted at 90 or 270 degrees, so the ratio must be inverted for a portrait display.
 *
 * The activity is locked to portrait, so display rotation is always 0 here. If portrait lock is
 * ever removed, use the full relative rotation instead:
 *   (sensorOrientation - displayRotationDegrees + 360) % 360
 */
val CameraCapabilities.previewDisplayAspect: Float
    get() {
        val w = previewSize.width.toFloat()
        val h = previewSize.height.toFloat()
        return if (sensorOrientation == 90 || sensorOrientation == 270) h / w else w / h
    }

private const val TAG = "CameraCapabilities"

object CameraCapabilitiesReader {

    /** Prefers a back-facing camera with MANUAL_SENSOR support; falls back to the first back camera. */
    fun selectCameraId(manager: CameraManager): String {
        val ids = manager.cameraIdList
        val backCameras = ids.filter { id ->
            val chars = manager.getCameraCharacteristics(id)
            chars.get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK
        }
        val candidates = backCameras.ifEmpty { ids.toList() }
        val manualCandidate = candidates.firstOrNull { id ->
            hasCapability(manager.getCameraCharacteristics(id), CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR)
        }
        return manualCandidate ?: candidates.firstOrNull() ?: ids.first()
    }

    fun read(manager: CameraManager, cameraId: String): CameraCapabilities {
        val chars = manager.getCameraCharacteristics(cameraId)
        val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            ?: error("Camera $cameraId has no stream configuration map")

        val sensitivityRange = chars.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)
            ?: Range(100, 1600)
        val exposureRange = chars.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)
            ?: Range(1_000_000L, 500_000_000L)
        val minFocus = chars.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE) ?: 0f
        val hyperfocal = chars.get(CameraCharacteristics.LENS_INFO_HYPERFOCAL_DISTANCE) ?: 0f
        val maxFrameDuration = chars.get(CameraCharacteristics.SENSOR_INFO_MAX_FRAME_DURATION) ?: 1_000_000_000L
        val hardwareLevel = chars.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL)
            ?: CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_LEGACY
        val activeArray = chars.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE) ?: Rect()
        val sensorOrientation = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 0

        val supportsManualSensor = hasCapability(chars, CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR)
        val supportsManualPost = hasCapability(chars, CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_POST_PROCESSING)
        val supportsRaw = hasCapability(chars, CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_RAW)

        val largestJpeg = largestSize(map.getOutputSizes(ImageFormat.JPEG))
        val largestYuv = largestSize(map.getOutputSizes(ImageFormat.YUV_420_888))
        val previewSize = pickPreviewSize(map, largestJpeg)

        val caps = CameraCapabilities(
            cameraId = cameraId,
            sensitivityRange = sensitivityRange,
            exposureTimeRangeNs = exposureRange,
            minFocusDistanceDiopters = minFocus,
            hyperfocalDistanceDiopters = hyperfocal,
            maxFrameDurationNs = maxFrameDuration,
            supportsManualSensor = supportsManualSensor,
            supportsManualPostProcessing = supportsManualPost,
            supportsRaw = supportsRaw,
            hardwareLevel = hardwareLevel,
            activeArraySize = activeArray,
            largestJpegSize = largestJpeg,
            largestYuvSize = largestYuv,
            previewSize = previewSize,
            sensorOrientation = sensorOrientation,
        )
        Log.i(TAG, "Camera $cameraId capabilities: $caps")
        return caps
    }

    private fun hasCapability(chars: CameraCharacteristics, capability: Int): Boolean {
        val caps = chars.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: return false
        return caps.contains(capability)
    }

    private fun largestSize(sizes: Array<Size>?): Size {
        return sizes?.maxByOrNull { it.width.toLong() * it.height } ?: Size(1920, 1080)
    }

    private fun pickPreviewSize(map: StreamConfigurationMap, jpegSize: Size): Size {
        val sizes = map.getOutputSizes(SurfaceHolder::class.java) ?: arrayOf(Size(1280, 720))
        val targetAspect = jpegSize.width.toDouble() / jpegSize.height.toDouble()
        val matching = sizes.filter { s ->
            val aspect = s.width.toDouble() / s.height.toDouble()
            kotlin.math.abs(aspect - targetAspect) < 0.01
        }
        val pool = matching.ifEmpty { sizes.toList() }
        return pool.filter { it.width <= 1920 && it.height <= 1080 }
            .maxByOrNull { it.width.toLong() * it.height }
            ?: pool.minByOrNull { it.width.toLong() * it.height }
            ?: Size(1280, 720)
    }
}
