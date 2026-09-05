package dev.hamster.framesampler.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import dev.hamster.framesampler.camera.CameraCapabilities
import dev.hamster.framesampler.model.LinearListAxis
import dev.hamster.framesampler.model.SweepConfig
import dev.hamster.framesampler.model.SweepDefaults
import kotlin.math.roundToInt

@Composable
fun ConfigureSheet(
    initialConfig: SweepConfig,
    caps: CameraCapabilities,
    onApply: (SweepConfig) -> Unit,
    onCancel: () -> Unit,
) {
    var draft by remember { mutableStateOf(initialConfig) }
    // Bumped whenever draft is replaced wholesale (Reset to defaults, Generate N evenly spaced)
    // so the text fields below re-seed their local editing buffers from the new values.
    var resetVersion by remember { mutableStateOf(0) }

    Dialog(
        onDismissRequest = onCancel,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        Surface(modifier = Modifier.fillMaxSize()) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .windowInsetsPadding(WindowInsets.systemBars),
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TextButton(onClick = onCancel) { Text("Cancel") }
                    Text("Configure sweep", style = MaterialTheme.typography.titleLarge)
                    Button(
                        onClick = { onApply(draft) },
                        enabled = draft.totalCaptures > 0,
                    ) { Text("Apply") }
                }
                HorizontalDivider()

                Column(
                    modifier = Modifier
                        .weight(1f)
                        .verticalScroll(rememberScrollState())
                        .padding(horizontal = 16.dp),
                ) {
                    GeometricAxisEditor(
                        title = "ISO (sensitivity)",
                        axis = draft.iso,
                        unitLabel = "ISO",
                        deviceRangeHint = "Device supports ${caps.sensitivityRange.lower} – ${caps.sensitivityRange.upper}",
                        resetKey = resetVersion,
                        onAxisChange = { draft = draft.copy(iso = it) },
                        formatValue = { it.roundToInt().toString() },
                    )
                    HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

                    GeometricAxisEditor(
                        title = "Exposure time",
                        axis = draft.exposure,
                        unitLabel = "ms",
                        deviceRangeHint = "Device supports ${nsToMs(caps.exposureTimeRangeNs.lower)} – ${nsToMs(caps.exposureTimeRangeNs.upper)} ms",
                        resetKey = resetVersion,
                        onAxisChange = { draft = draft.copy(exposure = it) },
                        formatValue = { "%.3f".format(it / 1_000_000.0) },
                    )
                    val exposureBudgetSec = draft.exposureValuesNs.sum() / 1_000_000_000.0
                    Text(
                        "Exposure budget for this axis: ${"%.1f".format(exposureBudgetSec)} s summed across all values",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

                    FocusAxisEditor(
                        caps = caps,
                        focus = draft.focus,
                        resetKey = resetVersion,
                        onFocusChange = { draft = draft.copy(focus = it) },
                        onFocusGenerated = { draft = draft.copy(focus = it); resetVersion++ },
                    )
                    HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

                    GlobalSettingsSection(
                        draft = draft,
                        resetKey = resetVersion,
                        onDraftChange = { draft = it },
                        onResetDefaults = { draft = SweepDefaults.forCamera(caps); resetVersion++ },
                    )
                }

                HorizontalDivider()
                SweepSummaryFooter(draft)
            }
        }
    }
}

@Composable
private fun FocusAxisEditor(
    caps: CameraCapabilities,
    focus: LinearListAxis,
    resetKey: Any,
    onFocusChange: (LinearListAxis) -> Unit,
    onFocusGenerated: (LinearListAxis) -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
        Text("Focal distance", style = MaterialTheme.typography.titleMedium)
        if (caps.minFocusDistanceDiopters <= 0f) {
            Text(
                "This camera has a fixed-focus lens.",
                style = MaterialTheme.typography.bodyMedium,
            )
        } else {
            Text(
                "Values in diopters (1/metres). 0.00 D = infinity, ${"%.2f".format(caps.minFocusDistanceDiopters)} D = closest focus.",
                style = MaterialTheme.typography.bodySmall,
            )

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
                label = { Text("Values, comma separated (diopters)") },
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Text),
                modifier = Modifier.fillMaxWidth(),
            )

            var genCountField by remember(resetKey) {
                mutableStateOf(TextFieldValue("10", selection = TextRange(2)))
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = genCountField,
                    onValueChange = { genCountField = it },
                    label = { Text("N") },
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.width(80.dp),
                )
                OutlinedButton(onClick = {
                    val n = genCountField.text.toIntOrNull()?.coerceAtLeast(1) ?: 10
                    onFocusGenerated(LinearListAxis(SweepDefaults.defaultFocusValuesForDiopters(caps.minFocusDistanceDiopters, n)))
                }) { Text("Generate N evenly spaced") }
            }

            Text(
                "${focus.values().size} value${if (focus.values().size == 1) "" else "s"} will be swept — " +
                    focus.values().joinToString(", ") { d -> "%.2f D (%s)".format(d, diopterToMetresLabel(d)) },
                style = MaterialTheme.typography.labelSmall,
            )
        }
    }
}

@Composable
private fun GlobalSettingsSection(
    draft: SweepConfig,
    resetKey: Any,
    onDraftChange: (SweepConfig) -> Unit,
    onResetDefaults: () -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
        Text("Global settings", style = MaterialTheme.typography.titleMedium)

        var framesToAverageField by remember(resetKey) {
            val initial = draft.framesToAverage.toString()
            mutableStateOf(TextFieldValue(initial, selection = TextRange(initial.length)))
        }
        OutlinedTextField(
            value = framesToAverageField,
            onValueChange = { newValue ->
                framesToAverageField = newValue
                newValue.text.toIntOrNull()?.coerceIn(1, 64)?.let { onDraftChange(draft.copy(framesToAverage = it)) }
            },
            label = { Text("Frames to average (n)") },
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            "Averaging is done on gamma-encoded JPEG pixels, so it reduces noise but is not radiometrically linear.",
            style = MaterialTheme.typography.bodySmall,
        )

        var settleFramesField by remember(resetKey) {
            val initial = draft.settleFrames.toString()
            mutableStateOf(TextFieldValue(initial, selection = TextRange(initial.length)))
        }
        OutlinedTextField(
            value = settleFramesField,
            onValueChange = { newValue ->
                settleFramesField = newValue
                newValue.text.toIntOrNull()?.coerceIn(0, 10)?.let { onDraftChange(draft.copy(settleFrames = it)) }
            },
            label = { Text("Settle frames") },
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        )
        Text(
            "Warm-up frames discarded after each settings change.",
            style = MaterialTheme.typography.bodySmall,
        )

        OutlinedButton(onClick = onResetDefaults, modifier = Modifier.padding(top = 12.dp)) {
            Text("Reset to defaults")
        }
    }
}

@Composable
private fun SweepSummaryFooter(config: SweepConfig) {
    val perFrameSec = (config.exposureValuesNs.average().takeIf { !it.isNaN() } ?: 0.0) / 1_000_000_000.0
    val estSeconds = config.totalFrames * (perFrameSec + 0.15)
    val estLabel = formatDuration(estSeconds)
    Box(modifier = Modifier.fillMaxWidth().padding(16.dp)) {
        Column {
            Text(
                "${config.isoValues.size} ISO × ${config.exposureValuesNs.size} exposures × " +
                    "${config.focusValues.size} focus distances = ${config.totalCaptures} captures",
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                "× ${config.framesToAverage} frame${if (config.framesToAverage == 1) "" else "s"} each = " +
                    "${config.totalFrames} frames · est. $estLabel",
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

private fun nsToMs(ns: Long): String = "%.3f".format(ns / 1_000_000.0)

private fun diopterToMetresLabel(d: Double): String = if (d <= 0.0) "∞" else "%.2f m".format(1.0 / d)

private fun formatDuration(totalSeconds: Double): String {
    val s = totalSeconds.roundToInt().coerceAtLeast(0)
    val m = s / 60
    val rem = s % 60
    return if (m > 0) "$m min $rem s" else "$rem s"
}
