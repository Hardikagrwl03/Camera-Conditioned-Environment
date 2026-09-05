package dev.hamster.framesampler.model

import android.graphics.ImageFormat

/**
 * How captured frames are encoded on disk.
 *
 * Note that [PNG] is **not** camera RAW: it is a lossless container for the fully processed 8-bit
 * image (demosaiced, white-balanced, gamma-encoded). What it avoids is JPEG's DCT compression
 * artifacts, which matters when the frames are measured rather than looked at. To get there the
 * frame is captured as uncompressed YUV and converted — capturing JPEG and re-wrapping it as PNG
 * would keep the artifacts and only make the files bigger.
 *
 * True RAW (16-bit Bayer sensor data, DNG) is a separate output that this app does not yet write.
 */
enum class OutputFormat(
    val label: String,
    val extension: String,
    /** The ImageReader format frames must be captured in to produce this output. */
    val readerFormat: Int,
) {
    JPEG("JPEG", "jpg", ImageFormat.JPEG),
    PNG("PNG", "png", ImageFormat.YUV_420_888),
}
