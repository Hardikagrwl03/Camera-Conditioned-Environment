package dev.hamster.framesampler.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * One accent per configuration attribute, tuned for contrast on both light and dark surfaces.
 *
 * Material You dynamic colour remains the foundation (surfaces, buttons, the capture button).
 * These accents are deliberately confined to small, high-signal elements — a tab's dot and value,
 * its sheet header, and selection state inside that sheet — so six hues read as a legend rather
 * than as noise, and so they never fight an arbitrary wallpaper-derived palette.
 */
enum class Accent(private val light: Color, private val dark: Color) {
    ISO(Color(0xFFF57C00), Color(0xFFFFB74D)),
    SHUTTER(Color(0xFF00838F), Color(0xFF4DD0E1)),
    FOCUS(Color(0xFF6A3DB8), Color(0xFFB39DDB)),
    FORMAT(Color(0xFF2E7D32), Color(0xFF81C784)),
    AVERAGE(Color(0xFFC2185B), Color(0xFFF48FB1)),
    SETTLE(Color(0xFF1565C0), Color(0xFF90CAF9));

    @Composable
    fun color(): Color = if (isSystemInDarkTheme()) dark else light

    /** Faint tinted container. Kept at <=16% alpha so onSurface text stays readable on it. */
    @Composable
    fun container(): Color = color().copy(alpha = if (isSystemInDarkTheme()) 0.16f else 0.10f)
}
