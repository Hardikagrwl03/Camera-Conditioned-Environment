package dev.hamster.framesampler.model

import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.roundToLong

enum class AxisMode { LIST, RANGE }

/**
 * n values from [start] to [end] inclusive, each a constant ratio apart.
 * Requires start > 0 and end > 0. n <= 1 returns [start].
 */
fun geometricSeries(start: Double, end: Double, n: Int): List<Double> {
    require(start > 0.0 && end > 0.0) { "geometric series needs positive endpoints" }
    if (n <= 1) return listOf(start)
    val ratio = (end / start).pow(1.0 / (n - 1))
    return (0 until n).map { start * ratio.pow(it.toDouble()) }
}

/** A sweep axis: either an explicit list of values, or a geometric range expanded to [count] values. */
data class GeometricAxis(
    val mode: AxisMode,
    val list: List<Double> = emptyList(),
    val start: Double = 1.0,
    val end: Double = 1.0,
    val count: Int = 1,
) {
    fun values(): List<Double> = when (mode) {
        AxisMode.LIST -> list.sorted().distinct()
        AxisMode.RANGE -> if (start <= 0.0 || end <= 0.0) emptyList() else geometricSeries(start, end, count).sorted().distinct()
    }
}

/** Focal distance has no geometric requirement — explicit list only (spec item 5). */
data class LinearListAxis(val list: List<Double> = emptyList()) {
    fun values(): List<Double> = list.sorted().distinct()
}

data class SweepConfig(
    val iso: GeometricAxis,
    val exposure: GeometricAxis,
    val focus: LinearListAxis,
    val framesToAverage: Int = 1,
    val settleFrames: Int = 2,
    val outputFormat: OutputFormat = OutputFormat.JPEG,
) {
    val isoValues: List<Int> get() = iso.values().map { it.roundToInt() }.distinct().sorted()
    val exposureValuesNs: List<Long> get() = exposure.values().map { it.roundToLong() }.distinct().sorted()
    val focusValues: List<Float> get() = focus.values().map { it.toFloat() }.distinct().sorted()
    val totalCaptures: Int get() = isoValues.size * exposureValuesNs.size * focusValues.size
    val totalFrames: Int get() = totalCaptures * framesToAverage
}
