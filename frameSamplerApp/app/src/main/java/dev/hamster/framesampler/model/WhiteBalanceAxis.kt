package dev.hamster.framesampler.model

import dev.hamster.framesampler.camera.MAX_KELVIN
import dev.hamster.framesampler.camera.MIN_KELVIN
import dev.hamster.framesampler.camera.uniformMiredSeries

/**
 * How white balance is chosen for a sweep.
 *
 * [AUTO] is the default and reproduces the app's original behaviour exactly: the request keeps
 * setting CONTROL_AWB_MODE = AUTO with CONTROL_AWB_LOCK = true and touches no colour-correction
 * keys at all. Because CONTROL_MODE is OFF for a manual capture, that means the frames inherit
 * whatever white balance the HAL had converged to before the sweep started, then hold it frozen.
 * [LIST] and [UNIFORM] instead command explicit channel gains per frame.
 */
enum class WhiteBalanceMode { AUTO, LIST, UNIFORM }

/**
 * The white balance sweep axis, parameterised by correlated colour temperature in kelvin.
 *
 * [UNIFORM] spaces its values evenly in mired rather than kelvin — see [uniformMiredSeries].
 */
data class WhiteBalanceAxis(
    val mode: WhiteBalanceMode = WhiteBalanceMode.AUTO,
    /** Explicit colour temperatures in kelvin, used by [WhiteBalanceMode.LIST]. */
    val list: List<Double> = emptyList(),
    val startKelvin: Double = 2000.0,
    val endKelvin: Double = 10000.0,
    val count: Int = 5,
) {
    /**
     * The values to sweep. A null element means "leave white balance alone", i.e. the AUTO path.
     *
     * AUTO always yields exactly one null, so a default configuration multiplies the frame count
     * by one and captures exactly what it did before this axis existed. An empty or unusable
     * manual selection falls back to the same single null rather than producing zero captures.
     */
    fun values(): List<Double?> = when (mode) {
        WhiteBalanceMode.AUTO -> listOf(null)
        WhiteBalanceMode.LIST ->
            list.map { it.coerceIn(MIN_KELVIN, MAX_KELVIN) }.sorted().distinct()
                .ifEmpty { listOf(null) }
        WhiteBalanceMode.UNIFORM ->
            uniformMiredSeries(startKelvin, endKelvin, count).ifEmpty { listOf(null) }
    }
}
