package dev.hamster.framesampler.model

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SweepConfigTest {

    @Test
    fun geometricSeries_sixValues_doublesEachStep() {
        val series = geometricSeries(100.0, 3200.0, 6)
        val expected = listOf(100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0)
        assertEquals(expected.size, series.size)
        expected.zip(series).forEach { (e, a) -> assertEquals(e, a, 1e-6) }
    }

    @Test
    fun geometricSeries_singleValue_returnsStart() {
        assertEquals(listOf(42.0), geometricSeries(42.0, 999.0, 1))
    }

    private fun defaultLikeConfig(): SweepConfig = SweepConfig(
        iso = GeometricAxis(mode = AxisMode.RANGE, start = 50.0, end = 51200.0, count = 10),
        exposure = GeometricAxis(mode = AxisMode.RANGE, start = 100_000.0, end = 500_000_000.0, count = 10),
        focus = LinearListAxis(list = SweepDefaults.defaultFocusValuesForDiopters(10f, 10)),
        framesToAverage = 1,
    )

    @Test
    fun defaultConfig_hasThousandTotalCaptures() {
        val config = defaultLikeConfig()
        assertEquals(10, config.isoValues.size)
        assertEquals(10, config.exposureValuesNs.size)
        assertEquals(10, config.focusValues.size)
        assertEquals(1000, config.totalCaptures)
        assertEquals(1000, config.totalFrames)
    }

    @Test
    fun defaultExposure_isClampedToFiveHundredMillis() {
        val config = defaultLikeConfig()
        assertTrue(config.exposureValuesNs.max() <= 500_000_000L)
    }

    @Test
    fun fixedFocusLens_producesSingleFocusValue() {
        val values = SweepDefaults.defaultFocusValuesForDiopters(0f, 10)
        assertEquals(listOf(0.0), values)
    }

    @Test
    fun listAxis_deduplicatesAndSorts() {
        val axis = GeometricAxis(mode = AxisMode.LIST, list = listOf(400.0, 100.0, 400.0, 200.0))
        assertEquals(listOf(100.0, 200.0, 400.0), axis.values())
    }
}
