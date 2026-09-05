package dev.hamster.framesampler.storage

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
)
