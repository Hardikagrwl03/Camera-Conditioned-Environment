package dev.hamster.lattice.camera

import kotlin.math.ln

/**
 * Colour-temperature maths for the white balance sweep axis.
 *
 * Deliberately free of Android types so it can be unit tested on the JVM: the test suite has been
 * broken before by classes like android.util.Range stubbing out to a throwing shim.
 *
 * **These gains are nominal, not colorimetric.** They come from a black-body approximation, not
 * from this sensor's SENSOR_CALIBRATION_TRANSFORM / SENSOR_COLOR_TRANSFORM pair. A kelvin value is therefore a label on a smooth, monotonic and
 * repeatable family of channel gains — not a claim that a frame is correctly balanced for that
 * illuminant. Analysis should use the gains read back into the manifest, not the requested kelvin.
 * A colorimetric version is possible later: this device reports reference illuminants D65 and
 * STANDARD_A with both calibration transforms.
 */

/**
 * Lowest colour temperature the approximation below stays valid for.
 *
 * The blue term goes to zero under 2000 K, which would make the blue gain infinite. Warmer than
 * this the axis simply has nothing meaningful to command.
 */
const val MIN_KELVIN = 2000.0

/** Highest colour temperature the approximation below stays valid for. */
const val MAX_KELVIN = 25000.0

/**
 * Gains are clamped here so a bad input can never build an unservable request. Nothing inside
 * [MIN_KELVIN]..[MAX_KELVIN] reaches it — the largest gain in range is about 12.8x, the blue
 * channel at 2000 K — so the clamp is a guard rail rather than part of the normal path.
 */
private const val MAX_GAIN = 16.0

/**
 * Micro reciprocal degrees: `10^6 / K`.
 *
 * Mired is the unit in which equal steps are equal colour shifts, the same way diopters are for
 * focus. A 1000 K step at 2000 K is a dramatic shift; the same step at 9000 K is nearly invisible.
 */
fun kelvinToMired(kelvin: Double): Double = 1e6 / kelvin

fun miredToKelvin(mired: Double): Double = 1e6 / mired

/**
 * Approximate linear RGB emitted by a black body at [kelvin], on a 0-255 scale.
 *
 * This is the Tanner Helland approximation with Neil Bartlett's refinement, fitted directly to
 * the black-body colours rather than derived by inverting the sRGB primaries.
 *
 * The fitted form is used **because** the principled route misbehaves: converting the Planckian
 * locus to CIE xy and inverting the sRGB primaries makes the blue primary's contribution collapse
 * at the warm end, so the reciprocal explodes — a blue gain of 126x at 2000 K and around 1e9 at
 * 1700 K. Every temperature below roughly 2850 K then hit the clamp and produced the same blue
 * gain, flattening the warm third of a sweep into near-duplicates. This form stays bounded and
 * strictly monotonic across the whole supported range.
 */
fun kelvinToLinearRgb(kelvin: Double): Triple<Double, Double, Double> {
    val k = kelvin.coerceIn(MIN_KELVIN, MAX_KELVIN)
    val t = k / 100.0
    val (r, g, b) = if (k <= 6600.0) {
        Triple(
            255.0,
            -155.25485562709179 - 0.44596950469579133 * (t - 2) + 104.49216199393888 * ln(t - 2),
            -254.76935184120902 + 0.8274096064007395 * (t - 10) + 115.67994401066147 * ln(t - 10),
        )
    } else {
        Triple(
            351.97690566805693 + 0.114206453784165 * (t - 55) - 40.25366309332127 * ln(t - 55),
            325.4494125711974 + 0.07943456536662342 * (t - 50) - 28.0852963507957 * ln(t - 50),
            255.0,
        )
    }
    return Triple(r.coerceIn(0.0, 255.0), g.coerceIn(0.0, 255.0), b.coerceIn(0.0, 255.0))
}

/**
 * Normalised R, G, B channel gains for a colour temperature. The smallest gain is always 1.0, so
 * white balance never darkens the frame — it only lifts the channels that need lifting.
 *
 * A channel is scaled by the inverse of how strongly the illuminant drives it, so a scene lit at
 * [kelvin] renders neutral. Warm light therefore lifts blue, cool light lifts red.
 */
fun kelvinToGains(kelvin: Double): Triple<Double, Double, Double> {
    val (r, g, b) = kelvinToLinearRgb(kelvin)
    val floor = 1e-6
    val inv = listOf(1.0 / r.coerceAtLeast(floor), 1.0 / g.coerceAtLeast(floor), 1.0 / b.coerceAtLeast(floor))
    val smallest = inv.min()
    val scaled = inv.map { (it / smallest).coerceIn(1.0, MAX_GAIN) }
    return Triple(scaled[0], scaled[1], scaled[2])
}

/**
 * [n] colour temperatures between the two endpoints, spaced evenly in **mired** and returned
 * ascending in kelvin.
 *
 * Even spacing in mired rather than kelvin is the whole point of the axis: over 2000-10000 K,
 * nine linear-in-kelvin values give mired steps ranging from 167 down to 11, a 15x variation in
 * how much the colour actually moves per step.
 */
fun uniformMiredSeries(startKelvin: Double, endKelvin: Double, n: Int): List<Double> {
    if (n <= 0) return emptyList()
    val lo = startKelvin.coerceIn(MIN_KELVIN, MAX_KELVIN)
    val hi = endKelvin.coerceIn(MIN_KELVIN, MAX_KELVIN)
    if (n == 1) return listOf(lo)

    val mLo = kelvinToMired(lo)
    val mHi = kelvinToMired(hi)
    return (0 until n)
        .map { miredToKelvin(mLo + (mHi - mLo) * it / (n - 1)) }
        .sorted()
        .distinct()
}
