package dev.hamster.framesampler.camera

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import java.io.ByteArrayOutputStream
import java.util.Arrays

/**
 * Accumulates n JPEG frames captured at the same configuration and produces their pixel-wise
 * average as a re-encoded JPEG. Averaging happens on gamma-encoded (decoded) RGB values, so it
 * reduces noise but is not radiometrically linear.
 *
 * Allocated once for the whole sweep and [reset] between configurations, since a full-resolution
 * accumulator is large (three IntArrays sized width*height, ~50 MB each at 12 MP).
 */
class JpegAverager(private val width: Int, private val height: Int) {
    private val pixelCount = width * height
    private val sumR = IntArray(pixelCount)
    private val sumG = IntArray(pixelCount)
    private val sumB = IntArray(pixelCount)
    private val scratch = IntArray(pixelCount)
    private var count = 0

    fun reset() {
        count = 0
        Arrays.fill(sumR, 0)
        Arrays.fill(sumG, 0)
        Arrays.fill(sumB, 0)
    }

    fun add(jpegBytes: ByteArray) {
        val bmp = BitmapFactory.decodeByteArray(jpegBytes, 0, jpegBytes.size)
            ?: error("Failed to decode JPEG frame for averaging")
        try {
            require(bmp.width == width && bmp.height == height) {
                "Frame size ${bmp.width}x${bmp.height} does not match averager size ${width}x$height"
            }
            bmp.getPixels(scratch, 0, width, 0, 0, width, height)
            for (i in 0 until pixelCount) {
                val p = scratch[i]
                sumR[i] += (p shr 16) and 0xFF
                sumG[i] += (p shr 8) and 0xFF
                sumB[i] += p and 0xFF
            }
            count++
        } finally {
            bmp.recycle()
        }
    }

    fun resultJpegBytes(quality: Int = 100): ByteArray {
        require(count > 0) { "No frames added to averager" }
        val n = count
        val half = n / 2
        val outPixels = IntArray(pixelCount)
        for (i in 0 until pixelCount) {
            val r = (sumR[i] + half) / n
            val g = (sumG[i] + half) / n
            val b = (sumB[i] + half) / n
            outPixels[i] = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
        }
        val bmp = Bitmap.createBitmap(outPixels, width, height, Bitmap.Config.ARGB_8888)
        val stream = ByteArrayOutputStream()
        try {
            bmp.compress(Bitmap.CompressFormat.JPEG, quality, stream)
        } finally {
            bmp.recycle()
        }
        return stream.toByteArray()
    }
}
