package dev.hamster.framesampler.camera

import android.media.Image

/**
 * Converts a YUV_420_888 [Image] to ARGB_8888 pixels.
 *
 * PNG output captures uncompressed YUV rather than JPEG precisely so the saved file carries no DCT
 * compression artifacts; re-wrapping a camera JPEG as PNG would keep the artifacts and only grow
 * the file. Chroma is still 4:2:0 subsampled — that is inherent to the capture format.
 *
 * Uses the standard integer BT.601 limited-range coefficients, and copies each plane into a
 * ByteArray first because per-pixel reads straight from a direct ByteBuffer are markedly slower.
 */
fun yuv420ToArgb(image: Image): IntArray {
    val width = image.width
    val height = image.height
    val out = IntArray(width * height)

    val yPlane = image.planes[0]
    val uPlane = image.planes[1]
    val vPlane = image.planes[2]

    val yBytes = ByteArray(yPlane.buffer.remaining()).also { yPlane.buffer.get(it) }
    val uBytes = ByteArray(uPlane.buffer.remaining()).also { uPlane.buffer.get(it) }
    val vBytes = ByteArray(vPlane.buffer.remaining()).also { vPlane.buffer.get(it) }

    val yRowStride = yPlane.rowStride
    val yPixelStride = yPlane.pixelStride
    val uRowStride = uPlane.rowStride
    val uPixelStride = uPlane.pixelStride
    val vRowStride = vPlane.rowStride
    val vPixelStride = vPlane.pixelStride

    var index = 0
    for (row in 0 until height) {
        val yRowBase = row * yRowStride
        val uvRowBase = (row shr 1)
        val uRowBase = uvRowBase * uRowStride
        val vRowBase = uvRowBase * vRowStride
        for (col in 0 until width) {
            val yIdx = yRowBase + col * yPixelStride
            val uvCol = col shr 1
            val uIdx = uRowBase + uvCol * uPixelStride
            val vIdx = vRowBase + uvCol * vPixelStride

            // Planes can be shorter than stride * height on the last row; clamp defensively.
            val y = if (yIdx < yBytes.size) (yBytes[yIdx].toInt() and 0xFF) - 16 else 0
            val u = if (uIdx < uBytes.size) (uBytes[uIdx].toInt() and 0xFF) - 128 else 0
            val v = if (vIdx < vBytes.size) (vBytes[vIdx].toInt() and 0xFF) - 128 else 0

            val y1192 = 1192 * if (y < 0) 0 else y
            var r = (y1192 + 1634 * v) shr 10
            var g = (y1192 - 833 * v - 400 * u) shr 10
            var b = (y1192 + 2066 * u) shr 10

            if (r < 0) r = 0 else if (r > 255) r = 255
            if (g < 0) g = 0 else if (g > 255) g = 255
            if (b < 0) b = 0 else if (b > 255) b = 255

            out[index++] = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
        }
    }
    return out
}
