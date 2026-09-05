package dev.hamster.framesampler.ui

import android.os.Environment
import android.os.StatFs
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import dev.hamster.framesampler.model.OutputFormat
import dev.hamster.framesampler.model.SweepConfig
import kotlin.math.roundToInt

/**
 * Single source of truth for how each configuration section is described, so the main-screen tiles
 * and the editing overlays can never disagree about what an axis currently holds.
 */

/** "50, 79 … 3200" — long axes elide the middle so both endpoints stay visible. */
fun elide(values: List<String>): String = when {
    values.isEmpty() -> "—"
    values.size <= 3 -> values.joinToString(", ")
    else -> values.take(2).joinToString(", ") + " … " + values.last()
}

fun isoSummary(config: SweepConfig): String = elide(config.isoValues.map { it.toString() })

fun exposureSummary(config: SweepConfig): String =
    if (config.exposureValuesNs.isEmpty()) "—"
    else elide(config.exposureValuesNs.map { trim(it / 1e6) }) + " ms"

fun focusSummary(config: SweepConfig): String {
    val v = config.focusValues
    return when {
        v.isEmpty() -> "—"
        v.size == 1 -> diopterLabel(v.first().toDouble())
        else -> "${diopterLabel(v.first().toDouble())} → ${diopterLabel(v.last().toDouble())}"
    }
}

/** The short value a tab displays: a count for the axes, the setting itself for the rest. */
fun sectionValue(section: ConfigSection, config: SweepConfig): String = when (section) {
    ConfigSection.ISO -> "${config.isoValues.size}"
    ConfigSection.SHUTTER -> "${config.exposureValuesNs.size}"
    ConfigSection.FOCUS -> "${config.focusValues.size}"
    ConfigSection.FORMAT -> config.outputFormat.label
    ConfigSection.AVERAGE -> "${config.framesToAverage}"
    ConfigSection.SETTLE -> "${config.settleFrames}"
}

/** The longer description a popup header shows next to its title. */
fun sectionDetail(section: ConfigSection, config: SweepConfig): String = when (section) {
    ConfigSection.ISO -> isoSummary(config)
    ConfigSection.SHUTTER -> exposureSummary(config)
    ConfigSection.FOCUS -> focusSummary(config)
    ConfigSection.FORMAT -> config.outputFormat.label
    ConfigSection.AVERAGE -> if (config.framesToAverage == 1) "single frame" else "${config.framesToAverage} frames averaged"
    ConfigSection.SETTLE -> "${config.settleFrames} warm-up frames"
}

/** Resolved value count for the three sweep axes; the scalar settings have none. */
fun sectionCount(section: ConfigSection, config: SweepConfig): Int? = when (section) {
    ConfigSection.ISO -> config.isoValues.size
    ConfigSection.SHUTTER -> config.exposureValuesNs.size
    ConfigSection.FOCUS -> config.focusValues.size
    else -> null
}

/** Shutter values are conventionally read as a reciprocal, e.g. "3.162 ms (1/316 s)". */
fun shutterLabel(ns: Long): String {
    val ms = ns / 1e6
    val seconds = ns / 1e9
    val reciprocal = if (seconds > 0) (1.0 / seconds).roundToInt() else 0
    return "${trim(ms)} ms (1/$reciprocal s)"
}

/** Formats a number with up to 3 decimals, trimming trailing zeros: 100.0 -> "100", 0.085 -> "0.085". */
fun trim(v: Double): String = "%.3f".format(v).trimEnd('0').trimEnd('.')

fun diopterLabel(d: Double): String =
    if (d <= 0.0) "0 D (∞)" else "%.2f D (%.2f m)".format(d, 1.0 / d)

/** Rough wall-clock estimate for a whole sweep: exposure plus fixed per-frame overhead. */
fun estimatedDuration(config: SweepConfig): String {
    val perFrameSec = (config.exposureValuesNs.average().takeIf { !it.isNaN() } ?: 0.0) / 1e9
    val s = (config.totalFrames * (perFrameSec + 0.15)).roundToInt().coerceAtLeast(0)
    val m = s / 60
    val rem = s % 60
    return if (m > 0) "$m min $rem s" else "$rem s"
}

/** Rough on-disk size of a whole sweep, in MB, at the configured format. */
fun estimatedSweepMb(config: SweepConfig): Long {
    val perFrameMb = if (config.outputFormat == OutputFormat.PNG) 30L else 5L
    return config.totalCaptures * perFrameMb
}

/** Free space on the volume the sweeps are written to, read once per composition. */
@Composable
fun rememberFreeBytes(): Long = remember {
    runCatching { StatFs(Environment.getExternalStorageDirectory().path).availableBytes }
        .getOrDefault(Long.MAX_VALUE)
}
