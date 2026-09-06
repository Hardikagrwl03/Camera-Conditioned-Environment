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
        focus = FocusAxis(mode = AxisMode.LIST, list = (0 until 10).map { it * 10.0 / 9 }, maxDiopters = 10.0),
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
    fun listAxis_deduplicatesAndSorts() {
        val axis = GeometricAxis(mode = AxisMode.LIST, list = listOf(400.0, 100.0, 400.0, 200.0))
        assertEquals(listOf(100.0, 200.0, 400.0), axis.values())
    }

    @Test
    fun downscaleFactors_onThisSensor_areOnlyTheExactDivisors() {
        // 4080 x 3060: 7, 8 and 9 each leave a remainder on one dimension.
        assertEquals(listOf(1, 2, 3, 4, 5, 6, 10), downscaleFactorsFor(4080, 3060))
    }

    @Test
    fun downscaleFactors_differForADifferentSensor() {
        // 4032 x 3024 (a common alternative) shares fewer divisors.
        assertEquals(listOf(1, 2, 3, 4, 6, 7, 8, 9), downscaleFactorsFor(4032, 3024))
    }

    @Test
    fun downscaleFactors_neverEmpty_andAlwaysIncludeOne() {
        val prime = downscaleFactorsFor(4099, 3061)
        assertEquals(listOf(1), prime)
    }

    @Test
    fun nearestDownscaleFactor_snapsRemovedValues() {
        val factors = downscaleFactorsFor(4080, 3060)
        assertEquals(6, nearestDownscaleFactor(7, factors))
        assertEquals(6, nearestDownscaleFactor(8, factors))
        assertEquals(10, nearestDownscaleFactor(9, factors))
        assertEquals(4, nearestDownscaleFactor(4, factors))
    }

    // ---- banded focus sampling ----

    private fun counts(inf: Int, far: Int, mid: Int, near: Int) = FocusBandCounts(inf, far, mid, near)

    @Test
    fun bandedFocus_totalIsTheFiveBoundariesPlusEveryCount() {
        // Counts are values added between edges; the five boundaries are always there as well.
        assertEquals(5 + 1 + 3 + 4 + 5, bandedFocusValues(counts(1, 3, 4, 5), 10.0).size)
    }

    @Test
    fun bandedFocus_alwaysContainsEveryBoundary() {
        for (c in listOf(counts(0, 0, 0, 0), counts(1, 3, 3, 3), counts(0, 7, 2, 9))) {
            val v = bandedFocusValues(c, 10.0)
            for (edge in listOf(0.0, 0.01, 0.1, 1.0, 10.0)) {
                assertTrue("missing $edge in $v", v.any { kotlin.math.abs(it - edge) < 1e-9 })
            }
        }
    }

    @Test
    fun bandedFocus_allZeroesGivesTheFiveBoundariesAlone() {
        val v = bandedFocusValues(counts(0, 0, 0, 0), 10.0)
        assertEquals(5, v.size)
        listOf(0.0, 0.01, 0.1, 1.0, 10.0).zip(v).forEach { (e, a) -> assertEquals(e, a, 1e-9) }
    }

    @Test
    fun bandedFocus_isAscendingAndDistinct() {
        val v = bandedFocusValues(counts(1, 6, 8, 12), 10.0)
        assertEquals(v.sorted(), v)
        assertEquals(v.distinct().size, v.size)
    }

    @Test
    fun bandedFocus_midBandRatioSpansTheDecadeInCountPlusOneSteps() {
        // n values added between 1 m and 10 m divide that decade into n + 1 equal ratio steps.
        val n = 4
        val v = bandedFocusValues(counts(0, 0, n, 0), 10.0).filter { it >= 0.1 && it <= 1.0 }
        assertEquals(n + 2, v.size)
        val expected = Math.pow(10.0, 1.0 / (n + 1))
        v.zipWithNext { a, b -> assertEquals(expected, b / a, 1e-9) }
    }

    @Test
    fun bandedFocus_geometricInDiopterIsGeometricInDistance() {
        // The design rests on this: D = 1/d, so a constant ratio in diopters is a constant
        // ratio in metres too. Without it, "geometric in distance" could not be built in
        // diopter space.
        val v = bandedFocusValues(counts(0, 0, 5, 0), 10.0).filter { it >= 0.1 && it <= 1.0 }
        val metres = v.map { 1.0 / it }
        val ratios = metres.zipWithNext { a, b -> b / a }
        ratios.forEach { assertEquals(ratios.first(), it, 1e-9) }
    }

    @Test
    fun bandedFocus_infinityBandIsLinearInDiopters() {
        // 100 m - infinity has no finite geometric endpoint, so it is spaced linearly.
        val v = bandedFocusValues(counts(1, 0, 0, 0), 10.0).filter { it <= 0.01 }
        val steps = v.zipWithNext { a, b -> b - a }
        steps.forEach { assertEquals(steps.first(), it, 1e-12) }
    }

    @Test
    fun bandedFocus_countsAreClampedToBandCapacity() {
        // Asking for 20 values across 0.01 D would request positions the actuator cannot resolve.
        val v = bandedFocusValues(counts(20, 0, 0, 0), 10.0)
        v.zipWithNext { a, b -> assertTrue("$a and $b are closer than one actuator step", b - a >= focusGridStep(a)) }
    }

    @Test
    fun bandedFocus_shortLensDropsAndTruncatesBands() {
        // A lens that focuses no closer than 2 m (0.5 D) has no 0.1 - 1 m band at all.
        val v = bandedFocusValues(counts(0, 2, 3, 4), 0.5)
        assertEquals(0.5, v.last(), 1e-9)
        assertTrue(v.none { it > 0.5 })
        assertEquals(3, focusBandsFor(0.5).size)
    }

    @Test
    fun bandedFocus_macroLensExtendsTheNearBand() {
        val bands = focusBandsFor(20.0)
        assertEquals(4, bands.size)
        assertEquals(20.0, bands.last().highDiopters, 1e-9)
        assertEquals(20.0, bandedFocusValues(counts(0, 0, 0, 4), 20.0).last(), 1e-9)
    }

    @Test
    fun bandedFocus_fixedFocusLensYieldsInfinityOnly() {
        assertEquals(listOf(0.0), bandedFocusValues(counts(3, 3, 3, 3), 0.0))
    }

    @Test
    fun bandedFocus_countExcludesTheBoundaries() {
        // A band's count is what it adds between its edges, never the edges themselves: asking
        // for 3 in the 1 - 10 m band yields 3 values strictly inside it.
        val inside = bandedFocusValues(counts(0, 0, 3, 0), 10.0).filter { it > 0.1 && it < 1.0 }
        assertEquals(3, inside.size)
    }

    @Test
    fun focusPresets_allProduceUsableSets() {
        for (preset in focusPresets()) {
            val v = bandedFocusValues(preset.counts, 10.0)
            assertTrue("${preset.label} produced ${v.size}", v.size >= 5)
            assertEquals(v.sorted().distinct(), v)
            assertEquals(0.0, v.first(), 1e-9)
            assertEquals(10.0, v.last(), 1e-9)
        }
    }

    @Test
    fun focusAxis_listModeSortsDedupesAndIgnoresBands() {
        val axis = FocusAxis(
            mode = AxisMode.LIST,
            list = listOf(1.0, 0.0, 1.0, 0.5),
            bands = counts(3, 3, 3, 3),
            maxDiopters = 10.0,
        )
        assertEquals(listOf(0.0, 0.5, 1.0), axis.values())
    }

    @Test
    fun focusAxis_uniformModeUsesBands() {
        val axis = FocusAxis(mode = AxisMode.RANGE, bands = counts(0, 0, 0, 0), maxDiopters = 10.0)
        assertEquals(5, axis.values().size)
    }
}
