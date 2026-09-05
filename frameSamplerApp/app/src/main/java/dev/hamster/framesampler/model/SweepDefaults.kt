package dev.hamster.framesampler.model

import dev.hamster.framesampler.camera.CameraCapabilities

object SweepDefaults {

    private const val DEFAULT_COUNT = 10
    private const val MIN_EXPOSURE_NS = 100_000L // 100 us
    private const val MAX_EXPOSURE_NS = 500_000_000L // 500 ms

    fun forCamera(caps: CameraCapabilities): SweepConfig {
        val isoAxis = GeometricAxis(
            mode = AxisMode.RANGE,
            start = caps.sensitivityRange.lower.toDouble(),
            end = caps.sensitivityRange.upper.toDouble(),
            count = DEFAULT_COUNT,
        )

        val expStart = caps.exposureTimeRangeNs.lower.coerceAtLeast(MIN_EXPOSURE_NS)
        val expEnd = caps.exposureTimeRangeNs.upper.coerceAtMost(MAX_EXPOSURE_NS).coerceAtLeast(expStart)
        val exposureAxis = GeometricAxis(
            mode = AxisMode.RANGE,
            start = expStart.toDouble(),
            end = expEnd.toDouble(),
            count = DEFAULT_COUNT,
        )

        val focusAxis = LinearListAxis(list = defaultFocusValues(caps))

        return SweepConfig(
            iso = isoAxis,
            exposure = exposureAxis,
            focus = focusAxis,
            framesToAverage = 1,
            settleFrames = 2,
        )
    }

    /** [count] values linear in diopters from 0 (infinity) to [caps.minFocusDistanceDiopters] (closest). */
    fun defaultFocusValues(caps: CameraCapabilities, count: Int = DEFAULT_COUNT): List<Double> =
        defaultFocusValuesForDiopters(caps.minFocusDistanceDiopters, count)

    /** Pure version of [defaultFocusValues] taking the diopter value directly (no CameraCapabilities needed). */
    fun defaultFocusValuesForDiopters(minFocusDiopters: Float, count: Int = DEFAULT_COUNT): List<Double> {
        if (minFocusDiopters <= 0f) return listOf(0.0)
        val max = minFocusDiopters.toDouble()
        if (count <= 1) return listOf(max)
        val step = max / (count - 1)
        return (0 until count).map { it * step }
    }
}
