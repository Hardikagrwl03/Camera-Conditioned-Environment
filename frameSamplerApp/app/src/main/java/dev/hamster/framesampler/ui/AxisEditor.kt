package dev.hamster.framesampler.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import dev.hamster.framesampler.camera.CameraCapabilities
import dev.hamster.framesampler.model.AxisMode
import dev.hamster.framesampler.model.GeometricAxis
import dev.hamster.framesampler.model.OutputFormat
import dev.hamster.framesampler.model.SweepConfig
import dev.hamster.framesampler.model.FocusAxis
import dev.hamster.framesampler.model.FocusBandCounts
import dev.hamster.framesampler.model.FocusPreset
import dev.hamster.framesampler.model.focusBandsFor
import dev.hamster.framesampler.model.focusPresets
import dev.hamster.framesampler.model.DEFAULT_BAND_COUNT
import dev.hamster.framesampler.model.downscaleFactorsFor
import dev.hamster.framesampler.model.nearestDownscaleFactor
import kotlin.math.roundToInt

/** Small, dim caption used for device limits and caveats. */
@Composable
fun Hint(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** A named preset that replaces an axis wholesale. */
data class AxisPreset(val label: String, val axis: GeometricAxis)

fun isoPresets(caps: CameraCapabilities): List<AxisPreset> {
    val lo = caps.sensitivityRange.lower.toDouble()
    val hi = caps.sensitivityRange.upper.toDouble()
    return listOf(
        AxisPreset("Full range", GeometricAxis(AxisMode.RANGE, start = lo, end = hi, count = 10)),
        AxisPreset("Low half", GeometricAxis(AxisMode.RANGE, start = lo, end = (hi / 8).coerceAtLeast(lo), count = 5)),
        AxisPreset("Native ${lo.toInt()}", GeometricAxis(AxisMode.LIST, list = listOf(lo))),
    )
}

fun shutterPresets(caps: CameraCapabilities): List<AxisPreset> {
    val lo = caps.exposureTimeRangeNs.lower.toDouble()
    val hi = caps.exposureTimeRangeNs.upper.toDouble()
    return listOf(
        AxisPreset("Full range", GeometricAxis(AxisMode.RANGE, start = lo.coerceAtLeast(100_000.0), end = hi, count = 10)),
        AxisPreset("Fast", GeometricAxis(AxisMode.RANGE, start = lo.coerceAtLeast(100_000.0), end = 10_000_000.0, count = 5)),
        AxisPreset("Slow", GeometricAxis(AxisMode.RANGE, start = 10_000_000.0, end = hi, count = 5)),
    )
}

/** Accent-outlined quick presets. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun PresetRow(presets: List<AxisPreset>, accentColor: Color, onPreset: (GeometricAxis) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        presets.forEach { preset ->
            Surface(
                onClick = { onPreset(preset.axis) },
                shape = RoundedCornerShape(50),
                color = Color.Transparent,
                border = BorderStroke(1.dp, accentColor.copy(alpha = 0.6f)),
            ) {
                Text(
                    preset.label,
                    style = MaterialTheme.typography.labelMedium,
                    color = accentColor,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
        }
    }
}

/** Every resolved value, wrapped — the sheet has room, so nothing is elided here. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ValueChips(labels: List<String>, accentColor: Color) {
    if (labels.isEmpty()) {
        Text(
            "No values — this axis is empty",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.error,
        )
        return
    }
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        labels.forEach { label ->
            Surface(shape = RoundedCornerShape(50), color = accentColor.copy(alpha = 0.14f)) {
                Text(
                    label,
                    style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
                )
            }
        }
    }
}

/**
 * Editor for one geometric sweep axis (ISO or shutter): a List/Uniform toggle, presets, the
 * matching inputs, and every resolved value as a chip.
 *
 * Each text field owns a local [TextFieldValue] buffer keyed by [resetKey] (bumped only on a
 * programmatic replacement such as a preset or "Reset all"). The plain (String, (String) -> Unit)
 * TextField overload discards the IME's own cursor/selection on every recomposition and has
 * Compose guess a new one, which shows up as the cursor jumping unpredictably (often to the
 * start) while typing. Using TextFieldValue and echoing the IME-reported selection back keeps
 * editing (including backspace) working reliably.
 */
@Composable
fun GeometricAxisEditor(
    axis: GeometricAxis,
    unitLabel: String,
    supportedHint: String,
    accentColor: Color,
    resetKey: Any,
    presets: List<AxisPreset>,
    onAxisChange: (GeometricAxis) -> Unit,
    onPreset: (GeometricAxis) -> Unit,
    formatValue: (Double) -> String,
    chipLabel: (Double) -> String,
) {
    val values = axis.values()
    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
        SegmentedButton(
            selected = axis.mode == AxisMode.LIST,
            onClick = { onAxisChange(axis.copy(mode = AxisMode.LIST)) },
            shape = SegmentedButtonDefaults.itemShape(index = 0, count = 2),
            icon = {},
        ) { Text("List") }
        SegmentedButton(
            selected = axis.mode == AxisMode.RANGE,
            onClick = { onAxisChange(axis.copy(mode = AxisMode.RANGE)) },
            shape = SegmentedButtonDefaults.itemShape(index = 1, count = 2),
            icon = {},
        ) { Text("Uniform") }
    }

    PresetRow(presets, accentColor, onPreset)

    when (axis.mode) {
        AxisMode.LIST -> {
            var field by remember(resetKey, axis.mode) {
                val initial = axis.list.joinToString(", ") { formatValue(it) }
                mutableStateOf(TextFieldValue(initial, selection = TextRange(initial.length)))
            }
            OutlinedTextField(
                value = field,
                onValueChange = { newValue ->
                    field = newValue
                    val parsed = newValue.text.split(",")
                        .map { it.trim() }
                        .filter { it.isNotEmpty() }
                        .mapNotNull { it.toDoubleOrNull() }
                    onAxisChange(axis.copy(list = parsed))
                },
                label = { Text("Values ($unitLabel), comma separated") },
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Text),
                // Wraps rather than scrolling horizontally: a single-line field on a long list
                // shows only its tail, which reads as if the leading values are missing.
                maxLines = 2,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        AxisMode.RANGE -> {
            var startField by remember(resetKey, axis.mode) {
                val initial = if (axis.start == 0.0) "" else formatValue(axis.start)
                mutableStateOf(TextFieldValue(initial, selection = TextRange(initial.length)))
            }
            var endField by remember(resetKey, axis.mode) {
                val initial = if (axis.end == 0.0) "" else formatValue(axis.end)
                mutableStateOf(TextFieldValue(initial, selection = TextRange(initial.length)))
            }
            var countField by remember(resetKey, axis.mode) {
                val initial = axis.count.toString()
                mutableStateOf(TextFieldValue(initial, selection = TextRange(initial.length)))
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = startField,
                    onValueChange = { newValue ->
                        startField = newValue
                        newValue.text.toDoubleOrNull()?.let { onAxisChange(axis.copy(start = it)) }
                    },
                    label = { Text("From") },
                    singleLine = true,
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = endField,
                    onValueChange = { newValue ->
                        endField = newValue
                        newValue.text.toDoubleOrNull()?.let { onAxisChange(axis.copy(end = it)) }
                    },
                    label = { Text("To") },
                    singleLine = true,
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = countField,
                    onValueChange = { newValue ->
                        countField = newValue
                        newValue.text.toIntOrNull()?.let { onAxisChange(axis.copy(count = it)) }
                    },
                    label = { Text("Steps") },
                    singleLine = true,
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.weight(0.7f),
                )
            }
        }
    }

    ValueChips(values.map(chipLabel), accentColor)
    Hint(supportedHint)
}

/** Accent-outlined band-count presets, the focus counterpart to [PresetRow]. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FocusPresetRow(presets: List<FocusPreset>, accentColor: Color, onPreset: (FocusBandCounts) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        presets.forEach { preset ->
            Surface(
                onClick = { onPreset(preset.counts) },
                shape = RoundedCornerShape(50),
                color = Color.Transparent,
                border = BorderStroke(1.dp, accentColor.copy(alpha = 0.6f)),
            ) {
                Text(
                    preset.label,
                    style = MaterialTheme.typography.labelMedium,
                    color = accentColor,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
        }
    }
}

/**
 * Focus editor: an explicit diopter list, or a set built from one count per distance band.
 *
 * Uniform mode divides the range at 1 m, 10 m and 100 m and spaces each band geometrically in
 * distance, so a fixed count per band means a fixed *ratio* between neighbouring distances rather
 * than a fixed number of metres. The boundaries themselves are always included, so no band can be
 * turned off entirely.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FocusAxisEditor(
    caps: CameraCapabilities,
    focus: FocusAxis,
    accentColor: Color,
    resetKey: Any,
    onFocusChange: (FocusAxis) -> Unit,
    onPreset: (FocusAxis) -> Unit,
) {
    if (caps.minFocusDistanceDiopters <= 0f) {
        Hint("This camera has a fixed-focus lens.")
        return
    }
    val values = focus.values()
    val maxD = focus.maxDiopters
    val bands = focusBandsFor(maxD)

    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
        SegmentedButton(
            selected = focus.mode == AxisMode.LIST,
            onClick = { onFocusChange(focus.copy(mode = AxisMode.LIST)) },
            shape = SegmentedButtonDefaults.itemShape(index = 0, count = 2),
            icon = {},
        ) { Text("List") }
        SegmentedButton(
            selected = focus.mode == AxisMode.RANGE,
            onClick = { onFocusChange(focus.copy(mode = AxisMode.RANGE)) },
            shape = SegmentedButtonDefaults.itemShape(index = 1, count = 2),
            icon = {},
        ) { Text("Uniform") }
    }

    when (focus.mode) {
        AxisMode.LIST -> {
            // Keyed by mode as well as resetKey so switching away and back rebuilds the buffer
            // from the axis rather than showing a stale string.
            var field by remember(resetKey, focus.mode) {
                val initial = focus.list.joinToString(", ") { "%.3f".format(it) }
                mutableStateOf(TextFieldValue(initial, selection = TextRange(initial.length)))
            }
            OutlinedTextField(
                value = field,
                onValueChange = { newValue ->
                    field = newValue
                    val parsed = newValue.text.split(",")
                        .map { it.trim() }
                        .filter { it.isNotEmpty() }
                        .mapNotNull { it.toDoubleOrNull() }
                    onFocusChange(focus.copy(list = parsed))
                },
                label = { Text("Values (diopters), comma separated") },
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Text),
                maxLines = 2,
                modifier = Modifier.fillMaxWidth(),
            )
        }

        AxisMode.RANGE -> {
            FocusPresetRow(focusPresets(), accentColor) { onPreset(focus.copy(bands = it)) }

            val counts = focus.bands.asList()
            bands.forEachIndexed { index, band ->
                val count = counts.getOrElse(index) { DEFAULT_BAND_COUNT }
                    .coerceIn(0, band.interiorCapacity)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(band.label, style = MaterialTheme.typography.bodyMedium)
                        Hint(
                            if (band.interiorCapacity < DEFAULT_BAND_COUNT) {
                                "$count added · lens fits only ${band.interiorCapacity} here"
                            } else {
                                "$count added between the edges"
                            },
                        )
                    }
                    Stepper(
                        value = count,
                        range = 0..band.interiorCapacity,
                        accentColor = accentColor,
                        onValueChange = { onFocusChange(focus.copy(bands = withBandCount(focus.bands, index, it))) },
                        modifier = Modifier,
                        compact = true,
                    )
                }
            }
        }
    }

    ValueChips(values.map { diopterLabel(it) }, accentColor)
    Hint(
        "0 D = infinity · ${"%.2f".format(maxD)} D = closest focus. The band edges — ∞, 100 m, " +
            "10 m, 1 m and closest focus — are always captured; each count adds that many values " +
            "between one pair of edges. Bands are spaced geometrically in distance; 100 m - ∞ is " +
            "linear in diopters, as distance has no finite end there.",
    )
}

/** Replaces one band's count positionally, matching the order [FocusBandCounts.asList] returns. */
private fun withBandCount(bands: FocusBandCounts, index: Int, value: Int): FocusBandCounts = when (index) {
    0 -> bands.copy(infinity = value)
    1 -> bands.copy(far = value)
    2 -> bands.copy(mid = value)
    else -> bands.copy(near = value)
}

/** Two selectable cards, with the storage cost of the choice made visible before the sweep. */
@Composable
fun FormatEditor(
    draft: SweepConfig,
    accentColor: Color,
    freeBytes: Long,
    onDraftChange: (SweepConfig) -> Unit,
) {
    OutputFormat.entries.forEach { format ->
        val selected = draft.outputFormat == format
        Surface(
            onClick = { onDraftChange(draft.copy(outputFormat = format)) },
            shape = RoundedCornerShape(18.dp),
            color = if (selected) accentColor.copy(alpha = 0.12f) else MaterialTheme.colorScheme.surfaceVariant,
            border = if (selected) BorderStroke(2.dp, accentColor) else null,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        format.label,
                        style = MaterialTheme.typography.titleMedium,
                        color = if (selected) accentColor else MaterialTheme.colorScheme.onSurface,
                    )
                    Text(
                        if (format == OutputFormat.JPEG) "~5 MB/frame" else "~30 MB/frame",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Text(
                    if (format == OutputFormat.JPEG) {
                        "Compressed, straight from the camera encoder. Lossy: 4:2:0 chroma subsampling " +
                            "and DCT quantization."
                    } else {
                        "Lossless encode, captured uncompressed so it carries no JPEG artifacts. Still " +
                            "processed 8-bit output, not sensor RAW."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }

    val needMb = estimatedSweepMb(draft)
    val freeMb = freeBytes / (1024 * 1024)
    val tight = needMb > freeMb
    Text(
        "${draft.totalFrames} frames ≈ $needMb MB · $freeMb MB free",
        style = MaterialTheme.typography.labelLarge,
        color = if (tight) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** Frame averaging: a stepper, plus the caveat that only bites in one specific combination. */
@Composable
fun AverageEditor(draft: SweepConfig, accentColor: Color, onDraftChange: (SweepConfig) -> Unit) {
    Stepper(
        value = draft.framesToAverage,
        range = 1..64,
        accentColor = accentColor,
        onValueChange = { onDraftChange(draft.copy(framesToAverage = it)) },
    )
    Text(
        if (draft.framesToAverage == 1) {
            "One frame per configuration. Raise this to average out sensor noise."
        } else {
            "Averages ${draft.framesToAverage} frames per configuration to reduce noise."
        },
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    // Only surfaced for the combination where it actually costs something.
    if (draft.framesToAverage > 1 && draft.outputFormat == OutputFormat.JPEG) {
        Surface(
            shape = RoundedCornerShape(12.dp),
            color = MaterialTheme.colorScheme.errorContainer,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    "With JPEG the averaged frame is re-encoded, adding a second generation of " +
                        "compression loss on top of the noise you just removed. PNG avoids it.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                )
                TextButton(onClick = { onDraftChange(draft.copy(outputFormat = OutputFormat.PNG)) }) {
                    Text("Switch to PNG")
                }
            }
        }
    }
}

/** Settle frames: a stepper, with the consequence of choosing zero spelled out. */
@Composable
fun SettleEditor(draft: SweepConfig, accentColor: Color, onDraftChange: (SweepConfig) -> Unit) {
    Stepper(
        value = draft.settleFrames,
        range = 0..10,
        accentColor = accentColor,
        onValueChange = { onDraftChange(draft.copy(settleFrames = it)) },
    )
    Text(
        "Warm-up frames discarded after each settings change, so the sensor has applied the new " +
            "ISO, shutter and focus before the frame that gets kept.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    if (draft.settleFrames == 0) {
        Text(
            "With 0 the first frame after each change may still carry the previous settings.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
        )
    }
}

/** Downscale factor: a discrete 1x-10x slider, so the whole range is one gesture. */
@Composable
fun DownscaleEditor(
    draft: SweepConfig,
    accentColor: Color,
    fullWidth: Int,
    fullHeight: Int,
    onDraftChange: (SweepConfig) -> Unit,
) {
    val factors = downscaleFactorsFor(fullWidth, fullHeight)
    Text(
        "${draft.downscale}x",
        style = MaterialTheme.typography.displaySmall,
        color = accentColor,
        modifier = Modifier.fillMaxWidth(),
        textAlign = TextAlign.Center,
    )
    // Switching output format can change the frame size and therefore which factors divide it
    // evenly; snap the draft if the current one is no longer offered.
    LaunchedEffect(factors, draft.downscale) {
        if (draft.downscale !in factors) {
            onDraftChange(draft.copy(downscale = nearestDownscaleFactor(draft.downscale, factors)))
        }
    }

    // The slider indexes the offered factors rather than mapping 1..10 continuously, so a factor
    // that would crop the frame simply has no position on the track.
    val index = factors.indexOf(draft.downscale)
        .let { if (it >= 0) it else factors.indexOf(nearestDownscaleFactor(draft.downscale, factors)) }
    Slider(
        value = index.toFloat(),
        onValueChange = { pos ->
            onDraftChange(draft.copy(downscale = factors[pos.roundToInt().coerceIn(factors.indices)]))
        },
        valueRange = 0f..(factors.size - 1).coerceAtLeast(1).toFloat(),
        steps = (factors.size - 2).coerceAtLeast(0),
        enabled = factors.size > 1,
        modifier = Modifier.fillMaxWidth(),
    )
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Hint("${factors.first()}x")
        Hint("${factors.last()}x")
    }
    Hint(
        "Offered: ${factors.joinToString(", ") { "${it}x" }} — the factors that divide " +
            "$fullWidth × $fullHeight exactly, so no pixels are cropped.",
    )

    val outW = if (draft.downscale <= 1) fullWidth else fullWidth / draft.downscale
    val outH = if (draft.downscale <= 1) fullHeight else fullHeight / draft.downscale
    val megapixels = outW.toLong() * outH / 1_000_000.0

    // Everything below the slider is deliberately fixed in line count. The sheet is anchored to
    // the bottom of the screen, so text that grew or shrank with the selected factor moved the
    // slider vertically under the user's finger. The source resolution lives in the "Offered"
    // line above instead of switching this line between two different phrasings.
    Text(
        "Output: $outW × $outH (${"%.1f".format(megapixels)} MP)",
        style = MaterialTheme.typography.bodyMedium,
    )
    Hint(
        "Downscaling averages each block of sensor pixels, which also lowers noise. Averaging is " +
            "on gamma-encoded values, so it is not radiometrically linear.",
    )
    if (draft.outputFormat == OutputFormat.JPEG) {
        Hint(
            "With JPEG, 1x writes the camera's frame untouched; any other factor decodes and " +
                "re-encodes it, costing one generation of compression.",
        )
    }
}
