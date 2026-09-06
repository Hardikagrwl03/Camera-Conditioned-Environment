package dev.hamster.framesampler.camera

import dev.hamster.framesampler.model.WhiteBalanceAxis
import dev.hamster.framesampler.model.WhiteBalanceMode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WhiteBalanceTest {

    @Test
    fun mired_roundTripsThroughKelvin() {
        assertEquals(200.0, kelvinToMired(5000.0), 1e-9)
        assertEquals(5000.0, miredToKelvin(kelvinToMired(5000.0)), 1e-9)
    }

    @Test
    fun uniformSeries_hasConstantMiredSteps() {
        val mired = uniformMiredSeries(2000.0, 10000.0, 9).map { kelvinToMired(it) }
        val steps = mired.zipWithNext { a, b -> b - a }
        steps.forEach { assertEquals(steps.first(), it, 1e-6) }
    }

    @Test
    fun uniformSeries_isNotConstantInKelvin() {
        // Pins the design decision: even spacing lives in mired, not kelvin. If someone
        // "simplifies" this to a linear kelvin ramp, this test fails rather than silently
        // wasting most of the sample budget at the blue end.
        val k = uniformMiredSeries(2000.0, 10000.0, 9)
        val steps = k.zipWithNext { a, b -> b - a }
        assertTrue("kelvin steps should vary widely", steps.max() / steps.min() > 5.0)
    }

    @Test
    fun uniformSeries_includesBothEndpoints() {
        val k = uniformMiredSeries(2000.0, 10000.0, 5)
        assertEquals(2000.0, k.first(), 1e-6)
        assertEquals(10000.0, k.last(), 1e-6)
        assertEquals(5, k.size)
    }

    @Test
    fun uniformSeries_singleValueReturnsStart() {
        assertEquals(listOf(3000.0), uniformMiredSeries(3000.0, 9000.0, 1))
    }

    @Test
    fun uniformSeries_isAscendingRegardlessOfEndpointOrder() {
        val k = uniformMiredSeries(10000.0, 2000.0, 5)
        assertEquals(k.sorted(), k)
        assertEquals(2000.0, k.first(), 1e-6)
    }

    @Test
    fun autoMode_yieldsExactlyOneNullValue() {
        // The property that keeps a default configuration's frame count unchanged.
        val v = WhiteBalanceAxis().values()
        assertEquals(1, v.size)
        assertNull(v.first())
    }

    @Test
    fun listMode_sortsDedupesAndClamps() {
        val v = WhiteBalanceAxis(
            mode = WhiteBalanceMode.LIST,
            list = listOf(6500.0, 3000.0, 6500.0, 999.0, 99999.0),
        ).values()
        assertEquals(listOf(MIN_KELVIN, 3000.0, 6500.0, MAX_KELVIN), v)
    }

    @Test
    fun emptyListFallsBackToAuto() {
        val v = WhiteBalanceAxis(mode = WhiteBalanceMode.LIST, list = emptyList()).values()
        assertEquals(1, v.size)
        assertNull(v.first())
    }

    @Test
    fun gains_areNormalisedToMinimumOne() {
        for (k in listOf(2000.0, 3000.0, 5000.0, 6500.0, 9000.0)) {
            val (r, g, b) = kelvinToGains(k)
            assertEquals("min gain at $k K", 1.0, minOf(r, g, b), 1e-9)
        }
    }

    @Test
    fun gains_moveMonotonicallyWithTemperature() {
        // Gains are normalised so the smallest is 1.0, which pins the red gain flat at 1.0 across
        // the warm half. The invariant that survives that normalisation is the red-to-blue ratio,
        // which must rise with temperature: warm light needs blue lifted, cool light needs red.
        val temps = (0..50).map { MIN_KELVIN + it * (MAX_KELVIN - MIN_KELVIN) / 50 }
        val ratio = temps.map { val (r, _, b) = kelvinToGains(it); r / b }
        ratio.zipWithNext { a, b -> assertTrue("R/B must not fall as kelvin rises", b >= a - 1e-9) }

        val (rWarm, _, bWarm) = kelvinToGains(2000.0)
        val (rCool, _, bCool) = kelvinToGains(10000.0)
        assertTrue("blue must be lifted at 2000 K", bWarm > 5.0)
        assertEquals("red is the reference channel at 2000 K", 1.0, rWarm, 1e-9)
        assertTrue("red must be lifted at 10000 K", rCool > 1.2)
        assertEquals("blue is the reference channel at 10000 K", 1.0, bCool, 1e-9)
    }

    @Test
    fun gains_areFiniteAndBoundedEverywhereInRange() {
        // Sweeps the whole range rather than sampling a few points, since the failure this guards
        // against — a reciprocal blowing up at one end — is exactly a local one.
        var largest = 1.0
        for (i in 0..500) {
            val k = MIN_KELVIN + i * (MAX_KELVIN - MIN_KELVIN) / 500
            val (r, g, b) = kelvinToGains(k)
            listOf(r, g, b).forEach {
                assertTrue("gain at $k K must be finite", it.isFinite())
                assertTrue("gain at $k K must be at least 1, was $it", it >= 1.0 - 1e-9)
                largest = maxOf(largest, it)
            }
        }
        // Nothing in range should reach the 16x guard rail; the true maximum is blue at 2000 K.
        assertTrue("largest in-range gain was $largest", largest < 13.0)
    }

    @Test
    fun gains_outsideTheRangeClampToTheEndpoints() {
        assertEquals(kelvinToGains(MIN_KELVIN), kelvinToGains(500.0))
        assertEquals(kelvinToGains(MAX_KELVIN), kelvinToGains(100000.0))
    }

    @Test
    fun neutralPointIsNearSixtySixHundredKelvin() {
        // Where the fit crosses over, all three channels should be close to unity gain: this is
        // the sanity check that the curve is anchored near daylight rather than drifting.
        val (r, g, b) = kelvinToGains(6600.0)
        listOf(r, g, b).forEach { assertEquals(1.0, it, 0.05) }
    }

    @Test
    fun linearRgb_staysWithinTheEightBitRange() {
        for (k in listOf(MIN_KELVIN, 3000.0, 6600.0, 10000.0, MAX_KELVIN)) {
            val (r, g, b) = kelvinToLinearRgb(k)
            listOf(r, g, b).forEach { assertTrue("channel at $k K was $it", it in 0.0..255.0) }
        }
    }
}
