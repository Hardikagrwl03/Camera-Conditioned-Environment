package dev.hamster.lattice.model

import dev.hamster.lattice.camera.CameraCapabilities
import kotlin.math.pow

/**
 * Values each band adds *between* its two boundaries by default, i.e. an equal split across all
 * four. The boundaries themselves are always present and are never counted here.
 */
const val DEFAULT_BAND_COUNT = 3

object SweepDefaults {

    /** Steps on each of the two geometric axes. */
    private const val DEFAULT_COUNT = 9

    private const val DEFAULT_ISO_LOW = 100.0
    private const val DEFAULT_ISO_HIGH = 1600.0

    private const val DEFAULT_EXPOSURE_LOW_NS = 1_000_000L    // 1 ms
    private const val DEFAULT_EXPOSURE_HIGH_NS = 100_000_000L // 100 ms

    private const val DEFAULT_SETTLE_FRAMES = 1
    private const val DEFAULT_AVERAGE_FRAMES = 1
    private const val DEFAULT_DOWNSCALE = 2
    private val DEFAULT_FORMAT = OutputFormat.PNG

    /**
     * A usable starting point rather than the widest possible sweep: a mid ISO range, exposures
     * from 1 to 100 ms, focus split equally across the four distance bands, and the device's own
     * white balance.
     *
     * Every value is clamped to what the camera actually reports, so a sensor with a narrower ISO
     * or exposure range gets a valid configuration rather than one the HAL will silently reject.
     */
    fun forCamera(caps: CameraCapabilities): SweepConfig {
        val isoLow = DEFAULT_ISO_LOW.coerceIn(
            caps.sensitivityRange.lower.toDouble(), caps.sensitivityRange.upper.toDouble(),
        )
        val isoHigh = DEFAULT_ISO_HIGH.coerceIn(isoLow, caps.sensitivityRange.upper.toDouble())
        val isoAxis = GeometricAxis(
            mode = AxisMode.RANGE,
            start = isoLow,
            end = isoHigh,
            count = DEFAULT_COUNT,
        )

        val expStart = DEFAULT_EXPOSURE_LOW_NS.coerceIn(
            caps.exposureTimeRangeNs.lower, caps.exposureTimeRangeNs.upper,
        )
        val expEnd = DEFAULT_EXPOSURE_HIGH_NS.coerceIn(expStart, caps.exposureTimeRangeNs.upper)
        val exposureAxis = GeometricAxis(
            mode = AxisMode.RANGE,
            start = expStart.toDouble(),
            end = expEnd.toDouble(),
            count = DEFAULT_COUNT,
        )

        // Uniform, weighted toward the near half. Counts are values added *between* each band's
        // edges, and the five edges themselves are always captured, so 0/1/2/2 resolves to ten
        // focus distances. The 100 m - infinity band gets none: it spans 0.01 D, under a tenth of
        // a pixel of defocus, so its two edges already cover everything optically distinct there.
        val focusAxis = FocusAxis(
            mode = AxisMode.RANGE,
            bands = FocusBandCounts(infinity = 0, far = 1, mid = 2, near = 2),
            maxDiopters = caps.minFocusDistanceDiopters.toDouble(),
        )

        // The offered downscale factors depend on the frame size the default format produces, so
        // snap rather than assume 2 divides this sensor evenly.
        val frame = if (DEFAULT_FORMAT == OutputFormat.PNG) caps.largestYuvSize else caps.largestJpegSize
        val downscale = nearestDownscaleFactor(
            DEFAULT_DOWNSCALE, downscaleFactorsFor(frame.width, frame.height),
        )

        return SweepConfig(
            iso = isoAxis,
            exposure = exposureAxis,
            focus = focusAxis,
            whiteBalance = WhiteBalanceAxis(),   // AUTO: the device's own balance, held fixed
            framesToAverage = DEFAULT_AVERAGE_FRAMES,
            settleFrames = DEFAULT_SETTLE_FRAMES,
            outputFormat = DEFAULT_FORMAT,
            downscale = downscale,
        )
    }
}

/**
 * Diopter boundaries of the focus bands: infinity, 100 m, 10 m and 1 m. The lens's closest focus
 * closes the last band, so the full set of boundaries is 0.1 m (or wherever the lens stops),
 * 1 m, 10 m, 100 m and infinity.
 */
private val BAND_EDGES = doubleArrayOf(0.0, 0.01, 0.1, 1.0)

/**
 * Diopters per step of the focus actuator, at a given focus position.
 *
 * Measured on this device by requesting closely spaced focus values at six positions across the
 * travel and reading back LENS_FOCUS_DISTANCE; two identical probe runs returned byte-identical
 * results, so the actuator is deterministic. The grid is not uniform — it shrinks about 11% from
 * infinity to closest focus — which this linear fit captures to within the measurement error.
 *
 * Used **only** to cap band counts and to warn. Generated focus values are never altered by it,
 * so on a device whose actuator differs this costs at most a suboptimal cap, never wrong data.
 */
fun focusGridStep(diopters: Double): Double =
    (0.004114 - 0.00004326 * diopters).coerceAtLeast(1e-4)

/** One distance band of the focus axis. */
data class FocusBand(
    val label: String,
    val lowDiopters: Double,
    val highDiopters: Double,
    /** False only for the infinity band, which is sampled linearly in diopters. */
    val geometric: Boolean,
) {
    /**
     * How many physically distinct lens positions this band can hold, boundaries included.
     *
     * The 100 m - infinity band spans only 0.01 D, about two actuator steps, so asking for more
     * values there yields duplicate frames rather than extra information.
     */
    val capacity: Int
        get() = ((highDiopters - lowDiopters) / focusGridStep((lowDiopters + highDiopters) / 2))
            .toInt().coerceAtLeast(1)

    /** Values that fit strictly between the boundaries, which take up one position between them. */
    val interiorCapacity: Int get() = (capacity - 1).coerceAtLeast(0)
}

/** "100 m", "0.1 m", "∞" — trailing zeros trimmed so band labels do not mix 1 m with 1.00 m. */
private fun distanceLabel(diopters: Double): String {
    if (diopters <= 0.0) return "∞"
    val metres = "%.2f".format(1.0 / diopters).trimEnd('0').trimEnd('.')
    return "$metres m"
}

/**
 * The focus bands available on a lens whose closest focus is [maxDiopters].
 *
 * Bands entirely beyond the lens's reach are dropped and the last surviving band is truncated to
 * [maxDiopters], so a lens stopping at 15 cm (6.67 D) gets a near band of "0.15 m - 1 m", and one
 * that cannot focus closer than 2 m gets no near band at all. A macro lens reaching 5 cm (20 D)
 * simply gets a wider near band.
 */
fun focusBandsFor(maxDiopters: Double): List<FocusBand> {
    if (maxDiopters <= 0.0) return emptyList()
    val bands = mutableListOf<FocusBand>()
    for (i in BAND_EDGES.indices) {
        val low = BAND_EDGES[i]
        if (low >= maxDiopters) break
        val high = (if (i + 1 < BAND_EDGES.size) BAND_EDGES[i + 1] else maxDiopters)
            .coerceAtMost(maxDiopters)
        if (high <= low) break
        bands += FocusBand(
            label = "${distanceLabel(high)} - ${distanceLabel(low)}",
            lowDiopters = low,
            highDiopters = high,
            geometric = low > 0.0,
        )
    }
    return bands
}

/**
 * Focus values built from per-band counts.
 *
 * Every band boundary -- infinity, 100 m, 10 m, 1 m and the lens's closest focus -- is always
 * present. A band's count is how many values it adds *between* its two boundaries, so 0 means
 * "just the edges" and the minimum result is the five boundaries alone. Adding to one band never
 * moves a value in another.
 *
 * Inner bands are spaced geometrically. Because D = 1/d, a geometric progression in diopters is
 * also a geometric progression in distance with the reciprocal ratio, so working in diopters --
 * the unit the camera takes and the manifest records -- gives geometric spacing in metres for
 * free. The infinity band is instead linear in diopters: distance is unbounded there, so it has
 * no finite geometric endpoint, and past 100 m distance has stopped being a meaningful
 * coordinate anyway.
 *
 * Counts are clamped to each band's [FocusBand.interiorCapacity], so the result never holds two
 * values the lens would round to the same actuator position.
 */
fun bandedFocusValues(bands: FocusBandCounts, maxDiopters: Double): List<Double> {
    if (maxDiopters <= 0.0) return listOf(0.0)
    val table = focusBandsFor(maxDiopters)
    if (table.isEmpty()) return listOf(0.0)

    val counts = bands.asList()
    val out = mutableListOf<Double>()
    table.forEachIndexed { i, band ->
        out += band.lowDiopters
        val n = counts.getOrElse(i) { DEFAULT_BAND_COUNT }.coerceIn(0, band.interiorCapacity)
        if (n == 0) return@forEachIndexed
        if (band.geometric) {
            // n interior values means n + 1 equal ratio steps across the band.
            val ratio = (band.highDiopters / band.lowDiopters).pow(1.0 / (n + 1))
            for (k in 1..n) out += band.lowDiopters * ratio.pow(k.toDouble())
        } else {
            val step = (band.highDiopters - band.lowDiopters) / (n + 1)
            for (k in 1..n) out += band.lowDiopters + step * k
        }
    }
    out += maxDiopters
    return out.sorted().distinct()
}

/** A named set of band counts, applied to the focus axis wholesale. */
data class FocusPreset(val label: String, val counts: FocusBandCounts)

/**
 * "Equal" is the default and splits the budget evenly. The other three concentrate it on one part
 * of the scene while still keeping every boundary, so no distance range is ever lost entirely.
 */
fun focusPresets(): List<FocusPreset> = listOf(
    FocusPreset("Equal", FocusBandCounts(DEFAULT_BAND_COUNT, DEFAULT_BAND_COUNT, DEFAULT_BAND_COUNT, DEFAULT_BAND_COUNT)),
    FocusPreset("Near", FocusBandCounts(infinity = 0, far = 1, mid = 3, near = 9)),
    FocusPreset("Mid", FocusBandCounts(infinity = 0, far = 2, mid = 9, near = 2)),
    FocusPreset("Far", FocusBandCounts(infinity = 1, far = 7, mid = 3, near = 1)),
)
