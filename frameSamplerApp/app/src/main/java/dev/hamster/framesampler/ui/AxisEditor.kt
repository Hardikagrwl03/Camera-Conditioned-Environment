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
import androidx.compose.ui.unit.dp
import dev.hamster.framesampler.camera.CameraCapabilities
import dev.hamster.framesampler.model.AxisMode
import dev.hamster.framesampler.model.GeometricAxis
import dev.hamster.framesampler.model.LinearListAxis
import dev.hamster.framesampler.model.OutputFormat
import dev.hamster.framesampler.model.SweepConfig
import dev.hamster.framesampler.model.SweepDefaults
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
 * Editor for one geometric sweep axis (ISO or shutter): a List/Geometric toggle, presets, the
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
        ) { Text("Geometric") }
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

/** Focus is an explicit diopter list, with a slider to space N values evenly — no keyboard needed. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FocusAxisEditor(
    caps: CameraCapabilities,
    focus: LinearListAxis,
    accentColor: Color,
    resetKey: Any,
    onFocusChange: (LinearListAxis) -> Unit,
    onFocusGenerated: (LinearListAxis) -> Unit,
) {
    if (caps.minFocusDistanceDiopters <= 0f) {
        Hint("This camera has a fixed-focus lens.")
        return
    }
    val values = focus.values()
    val maxD = caps.minFocusDistanceDiopters

    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        listOf(
            "Infinity only" to listOf(0.0),
            "Near + far" to listOf(0.0, maxD.toDouble()),
            "10 evenly" to SweepDefaults.defaultFocusValuesForDiopters(maxD, 10),
        ).forEach { (label, list) ->
            Surface(
                onClick = { onFocusGenerated(LinearListAxis(list)) },
                shape = RoundedCornerShape(50),
                color = Color.Transparent,
                border = BorderStroke(1.dp, accentColor.copy(alpha = 0.6f)),
            ) {
                Text(
                    label,
                    style = MaterialTheme.typography.labelMedium,
                    color = accentColor,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
        }
    }

    var field by remember(resetKey) {
        val initial = focus.list.joinToString(", ") { "%.2f".format(it) }
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
            onFocusChange(LinearListAxis(parsed))
        },
        label = { Text("Values (diopters), comma separated") },
        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Text),
        maxLines = 2,
        modifier = Modifier.fillMaxWidth(),
    )

    var sliderCount by remember(resetKey) { mutableStateOf(values.size.coerceIn(1, 20).toFloat()) }
    Text(
        "Space ${sliderCount.roundToInt()} evenly",
        style = MaterialTheme.typography.labelLarge,
        color = accentColor,
    )
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Slider(
            value = sliderCount,
            onValueChange = { sliderCount = it },
            valueRange = 1f..20f,
            steps = 18,
            modifier = Modifier.weight(1f),
        )
        OutlinedButton(onClick = {
            onFocusGenerated(LinearListAxis(SweepDefaults.defaultFocusValuesForDiopters(maxD, sliderCount.roundToInt())))
        }) { Text("Apply") }
    }

    ValueChips(values.map { diopterLabel(it) }, accentColor)
    Hint("0 D = infinity · ${"%.2f".format(maxD)} D = closest focus")
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
