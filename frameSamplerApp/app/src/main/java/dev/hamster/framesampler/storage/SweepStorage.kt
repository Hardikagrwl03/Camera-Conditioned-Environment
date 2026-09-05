package dev.hamster.framesampler.storage

import android.content.Context
import android.media.MediaScannerConnection
import android.os.Environment
import android.os.StatFs
import dev.hamster.framesampler.camera.CameraCapabilities
import dev.hamster.framesampler.model.OutputFormat
import dev.hamster.framesampler.model.SweepConfig
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private const val SWEEP_ROOT_DIR_NAME = "FramesSweep"
private const val JPEG_BYTES_PER_CAPTURE = 5L * 1024 * 1024
// A 12 MP lossless PNG runs several times the size of the equivalent JPEG.
private const val PNG_BYTES_PER_CAPTURE = 30L * 1024 * 1024

class SweepStorage {

    private val root: File
        get() = File(Environment.getExternalStorageDirectory(), SWEEP_ROOT_DIR_NAME)

    fun createSessionDir(): File {
        val name = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val dir = File(root, name)
        dir.mkdirs()
        return dir
    }

    fun writeJpeg(sessionDir: File, record: CaptureRecord, bytes: ByteArray) {
        val file = File(sessionDir, filenameFor(record))
        file.outputStream().use { it.write(bytes) }
    }

    fun availableBytes(): Long {
        val statPath = Environment.getExternalStorageDirectory()
        return try {
            StatFs(statPath.path).availableBytes
        } catch (e: Exception) {
            Long.MAX_VALUE
        }
    }

    fun estimatedBytesNeeded(totalCaptures: Int, format: OutputFormat): Long {
        val per = if (format == OutputFormat.PNG) PNG_BYTES_PER_CAPTURE else JPEG_BYTES_PER_CAPTURE
        return totalCaptures * per
    }

    fun hasEnoughSpace(totalCaptures: Int, format: OutputFormat): Boolean =
        availableBytes() > estimatedBytesNeeded(totalCaptures, format)

    fun writeManifest(
        sessionDir: File,
        caps: CameraCapabilities,
        config: SweepConfig,
        records: List<CaptureRecord>,
        appVersionName: String,
    ) {
        val manifest = JSONObject().apply {
            put("appVersion", appVersionName)
            put("sessionDir", sessionDir.name)
            put("camera", JSONObject().apply {
                put("cameraId", caps.cameraId)
                put("sensitivityRange", JSONArray(listOf(caps.sensitivityRange.lower, caps.sensitivityRange.upper)))
                put("exposureTimeRangeNs", JSONArray(listOf(caps.exposureTimeRangeNs.lower, caps.exposureTimeRangeNs.upper)))
                put("minFocusDistanceDiopters", caps.minFocusDistanceDiopters)
                put("hyperfocalDistanceDiopters", caps.hyperfocalDistanceDiopters)
                put("maxFrameDurationNs", caps.maxFrameDurationNs)
                put("supportsManualSensor", caps.supportsManualSensor)
                put("supportsManualPostProcessing", caps.supportsManualPostProcessing)
                put("supportsRaw", caps.supportsRaw)
                put("hardwareLevel", caps.hardwareLevel)
                put("largestJpegSize", "${caps.largestJpegSize.width}x${caps.largestJpegSize.height}")
                put("previewSize", "${caps.previewSize.width}x${caps.previewSize.height}")
                // Sizes above are in sensor coordinates; analysis needs this to orient the images.
                put("sensorOrientation", caps.sensorOrientation)
            })
            put("config", JSONObject().apply {
                put("isoValues", JSONArray(config.isoValues))
                put("exposureValuesNs", JSONArray(config.exposureValuesNs))
                put("focusValuesDiopters", JSONArray(config.focusValues.map { it.toDouble() }))
                put("outputFormat", config.outputFormat.label)
                put("framesToAverage", config.framesToAverage)
                put("settleFrames", config.settleFrames)
                put("totalCaptures", config.totalCaptures)
                put("totalFrames", config.totalFrames)
            })
            put("captures", JSONArray().apply {
                records.forEach { r -> put(recordToJson(r)) }
            })
        }
        File(sessionDir, "manifest.json").writeText(manifest.toString(2))
        writeCsv(sessionDir, records)
    }

    private fun recordToJson(r: CaptureRecord): JSONObject = JSONObject().apply {
        put("index", r.index)
        put("filename", filenameFor(r))
        put("requestedIso", r.requestedIso)
        put("actualIso", r.actualIso ?: JSONObject.NULL)
        put("requestedExposureNs", r.requestedExposureNs)
        put("actualExposureNs", r.actualExposureNs ?: JSONObject.NULL)
        put("requestedFocusDiopters", r.requestedFocusDiopters.toDouble())
        put("actualFocusDiopters", r.actualFocusDiopters?.toDouble() ?: JSONObject.NULL)
        put("framesAveraged", r.framesAveraged)
        put("settled", r.settled)
        put("timestampNs", r.timestampNs)
    }

    private fun writeCsv(sessionDir: File, records: List<CaptureRecord>) {
        val sb = StringBuilder()
        sb.append("index,filename,requestedIso,actualIso,requestedExposureNs,actualExposureNs,")
            .append("requestedFocusDiopters,actualFocusDiopters,framesAveraged,settled,timestampNs\n")
        records.forEach { r ->
            sb.append(r.index).append(',')
                .append(filenameFor(r)).append(',')
                .append(r.requestedIso).append(',')
                .append(r.actualIso ?: "").append(',')
                .append(r.requestedExposureNs).append(',')
                .append(r.actualExposureNs ?: "").append(',')
                .append(r.requestedFocusDiopters).append(',')
                .append(r.actualFocusDiopters ?: "").append(',')
                .append(r.framesAveraged).append(',')
                .append(r.settled).append(',')
                .append(r.timestampNs).append('\n')
        }
        File(sessionDir, "metadata.csv").writeText(sb.toString())
    }

    fun scanSessionDir(context: Context, sessionDir: File) {
        val files = sessionDir.listFiles()?.map { it.absolutePath }?.toTypedArray() ?: arrayOf(sessionDir.absolutePath)
        MediaScannerConnection.scanFile(context, files, null, null)
    }

    companion object {
        /** iso<ISO>_exp<EXPOSURE_US>us_fd<FOCUS_DIOPTERS>D_avg<N>_<INDEX>.<ext> */
        fun filenameFor(record: CaptureRecord): String {
            val isoStr = record.requestedIso.toString().padStart(4, '0')
            val expUs = record.requestedExposureNs / 1000
            val expStr = expUs.toString().padStart(6, '0')
            val fdStr = String.format(Locale.US, "%.2f", record.requestedFocusDiopters).replace('.', 'p')
            val idxStr = record.index.toString().padStart(4, '0')
            return "iso${isoStr}_exp${expStr}us_fd${fdStr}D_avg${record.framesAveraged}_$idxStr.${record.extension}"
        }
    }
}
