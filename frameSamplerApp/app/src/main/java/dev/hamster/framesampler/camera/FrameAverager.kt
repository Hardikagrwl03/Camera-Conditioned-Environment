package dev.hamster.framesampler.camera

import android.graphics.Bitmap
import dev.hamster.framesampler.model.OutputFormat
import java.io.ByteArrayOutputStream
import java.util.Arrays

/**
 * Accumulates n frames captured at the same configuration and produces their pixel-wise average.
 *
 * Averaging happens on gamma-encoded RGB values, so it reduces noise but is not radiometrically
 * linear. Allocated once for a whole sweep and [reset] between configurations, since a
 * full-resolution accumulator is large (three IntArrays of width*height, ~50 MB each at 12 MP).
 */
class FrameAverager(private val width: Int, private val height: Int) {
    private val pixelCount = width * height
    private val sumR = IntArray(pixelCount)
    private val sumG = IntArray(pixelCount)
    private val sumB = IntArray(pixelCount)
    private var count = 0

    fun reset() {
        count = 0
        Arrays.fill(sumR, 0)
        Arrays.fill(sumG, 0)
        Arrays.fill(sumB, 0)
    }

    fun add(pixels: IntArray) {
        require(pixels.size == pixelCount) {
            "Frame has ${pixels.size} pixels, averager expects $pixelCount"
        }
        for (i in 0 until pixelCount) {
            val p = pixels[i]
            sumR[i] += (p shr 16) and 0xFF
            sumG[i] += (p shr 8) and 0xFF
            sumB[i] += p and 0xFF
        }
        count++
    }

    /** The averaged frame as ARGB_8888 pixels. */
    fun averagedPixels(): IntArray {
        require(count > 0) { "No frames added to averager" }
        val n = count
        val half = n / 2
        val out = IntArray(pixelCount)
        for (i in 0 until pixelCount) {
            val r = (sumR[i] + half) / n
            val g = (sumG[i] + half) / n
            val b = (sumB[i] + half) / n
            out[i] = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
        }
        return out
    }
}

/** Encodes ARGB_8888 pixels in the requested output format. */
fun encodePixels(pixels: IntArray, width: Int, height: Int, format: OutputFormat): ByteArray {
    val bmp = Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888)
    val stream = ByteArrayOutputStream()
    try {
        when (format) {
            // Quality is ignored for PNG, which is lossless.
            OutputFormat.PNG -> bmp.compress(Bitmap.CompressFormat.PNG, 100, stream)
            OutputFormat.JPEG -> bmp.compress(Bitmap.CompressFormat.JPEG, 100, stream)
        }
    } finally {
        bmp.recycle()
    }
    return stream.toByteArray()
}
