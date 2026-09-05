package dev.hamster.framesampler.camera

/** ARGB_8888 pixels with their dimensions. */
data class PixelFrame(val pixels: IntArray, val width: Int, val height: Int) {
    override fun equals(other: Any?): Boolean =
        other is PixelFrame && width == other.width && height == other.height && pixels.contentEquals(other.pixels)

    override fun hashCode(): Int = 31 * (31 * pixels.contentHashCode() + width) + height
}

/**
 * Shrinks a frame by an integer factor, averaging each factor x factor block of pixels.
 *
 * A box average rather than a resampling filter: it is exactly pixel binning, so it preserves the
 * mean signal of each block and lowers read noise by the factor, which is what a measurement rig
 * wants. Note the averaging happens on gamma-encoded values, so like frame averaging it reduces
 * noise without being radiometrically linear.
 *
 * Any remainder rows/columns that do not fill a whole block are cropped, so output dimensions are
 * width/factor by height/factor.
 */
fun boxDownscale(pixels: IntArray, width: Int, height: Int, factor: Int): PixelFrame {
    if (factor <= 1) return PixelFrame(pixels, width, height)

    val outW = width / factor
    val outH = height / factor
    require(outW > 0 && outH > 0) { "Downscale ${factor}x leaves no pixels for a ${width}x$height frame" }

    val out = IntArray(outW * outH)
    val blockPixels = factor * factor

    for (oy in 0 until outH) {
        val srcYStart = oy * factor
        for (ox in 0 until outW) {
            val srcXStart = ox * factor
            var sumR = 0
            var sumG = 0
            var sumB = 0
            for (dy in 0 until factor) {
                var idx = (srcYStart + dy) * width + srcXStart
                for (dx in 0 until factor) {
                    val p = pixels[idx++]
                    sumR += (p shr 16) and 0xFF
                    sumG += (p shr 8) and 0xFF
                    sumB += p and 0xFF
                }
            }
            val half = blockPixels / 2
            val r = (sumR + half) / blockPixels
            val g = (sumG + half) / blockPixels
            val b = (sumB + half) / blockPixels
            out[oy * outW + ox] = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
        }
    }
    return PixelFrame(out, outW, outH)
}

/** Output dimensions a frame will have after [factor] downscaling, without doing the work. */
fun downscaledSize(width: Int, height: Int, factor: Int): Pair<Int, Int> =
    if (factor <= 1) width to height else (width / factor) to (height / factor)
