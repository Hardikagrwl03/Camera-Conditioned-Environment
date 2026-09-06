package dev.hamster.lattice.storage

/** One row of the sweep manifest: what was requested for a capture vs. what the sensor reported. */
data class CaptureRecord(
    val index: Int,
    val requestedIso: Int,
    val actualIso: Int?,
    val requestedExposureNs: Long,
    val actualExposureNs: Long?,
    val requestedFocusDiopters: Float,
    val actualFocusDiopters: Float?,
    val framesAveraged: Int,
    val settled: Boolean,
    val timestampNs: Long,
    /** File extension for the configured output format, e.g. "jpg" or "png". */
    val extension: String = "jpg",
    /** Box-downscale factor applied before encoding; 1 is full sensor resolution. */
    val downscale: Int = 1,
    val outputWidth: Int = 0,
    val outputHeight: Int = 0,
    /** Commanded colour temperature, or null when white balance was left on the device's AUTO. */
    val requestedKelvin: Double? = null,
    /** Gains actually applied, read back from the result: [r, gEven, gOdd, b]. Null if unreported. */
    val actualColorGains: List<Float>? = null,
    /** CONTROL_AWB_STATE from the result, for diagnosing an AUTO capture after the fact. */
    val awbState: Int? = null,
)
