package dev.hamster.framesampler.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AssistChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import dev.hamster.framesampler.model.AxisMode
import dev.hamster.framesampler.model.GeometricAxis

/**
 * Editor for one geometric sweep axis (ISO or exposure): a List/Range toggle, the corresponding
 * input fields, and read-only chips previewing the resolved values.
 *
 * Each text field owns a local [TextFieldValue] buffer keyed by [resetKey] (bumped only on a
 * programmatic replacement such as "Reset to defaults"). The plain (String, (String) -> Unit)
 * TextField overload discards the IME's own cursor/selection on every recomposition and has
 * Compose guess a new one, which shows up as the cursor jumping unpredictably (often to the
 * start) while typing. Using TextFieldValue and echoing the IME-reported selection back keeps
 * editing (including backspace) working reliably.
 */
@Composable
fun GeometricAxisEditor(
    title: String,
    axis: GeometricAxis,
    unitLabel: String,
    deviceRangeHint: String,
    resetKey: Any,
    onAxisChange: (GeometricAxis) -> Unit,
    formatValue: (Double) -> String = { it.toString() },
) {
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium)
        Text(deviceRangeHint, style = MaterialTheme.typography.bodySmall)

        SingleChoiceSegmentedButtonRow(modifier = Modifier.padding(vertical = 4.dp)) {
            SegmentedButton(
                selected = axis.mode == AxisMode.LIST,
                onClick = { onAxisChange(axis.copy(mode = AxisMode.LIST)) },
                shape = SegmentedButtonDefaults.itemShape(index = 0, count = 2),
            ) { Text("List") }
            SegmentedButton(
                selected = axis.mode == AxisMode.RANGE,
                onClick = { onAxisChange(axis.copy(mode = AxisMode.RANGE)) },
                shape = SegmentedButtonDefaults.itemShape(index = 1, count = 2),
            ) { Text("Range (geometric)") }
        }

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
                    label = { Text("Values, comma separated ($unitLabel)") },
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Text),
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
                        label = { Text("Start") },
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedTextField(
                        value = endField,
                        onValueChange = { newValue ->
                            endField = newValue
                            newValue.text.toDoubleOrNull()?.let { onAxisChange(axis.copy(end = it)) }
                        },
                        label = { Text("End") },
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedTextField(
                        value = countField,
                        onValueChange = { newValue ->
                            countField = newValue
                            newValue.text.toIntOrNull()?.let { onAxisChange(axis.copy(count = it)) }
                        },
                        label = { Text("Count") },
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.weight(0.6f),
                    )
                }
            }
        }

        val values = axis.values()
        Text(
            "${values.size} value${if (values.size == 1) "" else "s"} will be swept",
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.padding(top = 4.dp),
        )
        LazyRow(horizontalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.padding(top = 4.dp)) {
            items(values) { v ->
                AssistChip(onClick = {}, label = { Text(formatValue(v)) })
            }
        }
    }
}
