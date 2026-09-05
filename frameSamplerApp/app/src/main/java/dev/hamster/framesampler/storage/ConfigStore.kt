package dev.hamster.framesampler.storage

import android.content.Context
import android.util.Log
import dev.hamster.framesampler.camera.CameraCapabilities
import dev.hamster.framesampler.model.AxisMode
import dev.hamster.framesampler.model.GeometricAxis
import dev.hamster.framesampler.model.LinearListAxis
import dev.hamster.framesampler.model.downscaleFactorsFor
import dev.hamster.framesampler.model.nearestDownscaleFactor
import dev.hamster.framesampler.model.OutputFormat
import dev.hamster.framesampler.model.SweepConfig
import org.json.JSONArray
import org.json.JSONObject

private const val PREFS_NAME = "sweep_config"
private const val KEY_CONFIG = "config_json"
private const val TAG = "ConfigStore"

/** Bumped when the stored shape changes; older payloads are discarded rather than migrated. */
private const val SCHEMA_VERSION = 1

/**
 * Persists the sweep configuration across app restarts.
 *
 * The payload records a fingerprint of the camera it was built for. Sweep values are bounded by
 * the sensor's own ISO / exposure / focus ranges, so a configuration restored onto a different
 * camera would be meaningless — in that case the stored config is ignored and defaults are used.
 *
 * Every read is defensive: corrupt, truncated or older payloads return null rather than throwing,
 * so a bad file can never stop the app from starting.
 */
class ConfigStore(context: Context) {

    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun save(config: SweepConfig, caps: CameraCapabilities) {
        val json = JSONObject().apply {
            put("version", SCHEMA_VERSION)
            put("camera", fingerprint(caps))
            put("iso", axisToJson(config.iso))
            put("exposure", axisToJson(config.exposure))
            put("focus", JSONArray(config.focus.list))
            put("framesToAverage", config.framesToAverage)
            put("settleFrames", config.settleFrames)
            put("outputFormat", config.outputFormat.name)
            put("downscale", config.downscale)
        }
        prefs.edit().putString(KEY_CONFIG, json.toString()).apply()
    }

    /** The stored configuration, or null when there is none, it is unreadable, or it belongs to another camera. */
    fun load(caps: CameraCapabilities): SweepConfig? {
        val raw = prefs.getString(KEY_CONFIG, null) ?: return null
        return try {
            val json = JSONObject(raw)
            if (json.optInt("version") != SCHEMA_VERSION) return null
            if (json.optString("camera") != fingerprint(caps)) return null

            val format = OutputFormat.entries
                .firstOrNull { it.name == json.optString("outputFormat") }
                ?: OutputFormat.JPEG
            // The valid factors depend on the frame size the stored format will produce.
            val frame = if (format == OutputFormat.PNG) caps.largestYuvSize else caps.largestJpegSize
            val factors = downscaleFactorsFor(frame.width, frame.height)

            SweepConfig(
                iso = axisFromJson(json.getJSONObject("iso")),
                exposure = axisFromJson(json.getJSONObject("exposure")),
                focus = LinearListAxis(doubleList(json.getJSONArray("focus"))),
                framesToAverage = json.getInt("framesToAverage").coerceIn(1, 64),
                settleFrames = json.getInt("settleFrames").coerceIn(0, 10),
                outputFormat = format,
                downscale = nearestDownscaleFactor(json.optInt("downscale", 1), factors),
            ).takeIf { it.totalCaptures > 0 }
        } catch (e: Exception) {
            Log.w(TAG, "Stored configuration unreadable, falling back to defaults", e)
            null
        }
    }

    fun clear() = prefs.edit().remove(KEY_CONFIG).apply()

    private fun axisToJson(axis: GeometricAxis): JSONObject = JSONObject().apply {
        put("mode", axis.mode.name)
        put("list", JSONArray(axis.list))
        put("start", axis.start)
        put("end", axis.end)
        put("count", axis.count)
    }

    private fun axisFromJson(json: JSONObject): GeometricAxis = GeometricAxis(
        mode = AxisMode.entries.firstOrNull { it.name == json.optString("mode") } ?: AxisMode.RANGE,
        list = doubleList(json.getJSONArray("list")),
        start = json.getDouble("start"),
        end = json.getDouble("end"),
        count = json.getInt("count"),
    )

    private fun doubleList(array: JSONArray): List<Double> =
        (0 until array.length()).map { array.getDouble(it) }

    /**
     * Identifies the camera a configuration was built for. The id alone is not enough — it is "0"
     * on nearly every device — so the sensor's actual ranges are folded in.
     */
    private fun fingerprint(caps: CameraCapabilities): String = buildString {
        append(caps.cameraId).append('|')
        append(caps.sensitivityRange.lower).append('-').append(caps.sensitivityRange.upper).append('|')
        append(caps.exposureTimeRangeNs.lower).append('-').append(caps.exposureTimeRangeNs.upper).append('|')
        append(caps.minFocusDistanceDiopters)
    }
}
