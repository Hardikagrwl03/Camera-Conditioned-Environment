package dev.hamster.framesampler.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.BottomSheetDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import dev.hamster.framesampler.camera.CameraCapabilities
import dev.hamster.framesampler.model.OutputFormat
import dev.hamster.framesampler.model.SweepConfig
import dev.hamster.framesampler.model.SweepDefaults
import kotlin.math.roundToInt

/**
 * Editing popup for one [ConfigSection], covering only the lower part of the screen so the preview
 * stays visible above it.
 *
 * The draft starts as a copy of the live config and only this section's field is touched, so
 * applying one section can never clobber another.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConfigSheet(
    section: ConfigSection,
    initialConfig: SweepConfig,
    caps: CameraCapabilities,
    onApply: (SweepConfig) -> Unit,
    onCancel: () -> Unit,
) {
    // Keyed on section so opening a different tab starts from the live config, not a stale draft.
    var draft by remember(section) { mutableStateOf(initialConfig) }
    var resetVersion by remember(section) { mutableStateOf(0) }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onCancel,
        sheetState = sheetState,
        shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp),
        dragHandle = { BottomSheetDefaults.DragHandle() },
        // The sheet must not consume IME insets itself — the content column does, via imePadding.
        // Doing exactly one of the two leaves the inputs hidden behind the keyboard.
        contentWindowInsets = { WindowInsets(0) },
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .imePadding()
                .padding(horizontal = 20.dp)
                .padding(bottom = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            SheetHeader(section, draft)

            // The scrollable body yields height to the keyboard so the footer's Apply/Cancel
            // never get clipped: a fixed cap leaves the footer off-screen once the IME is up.
            val density = LocalDensity.current
            val imeDp = with(density) { WindowInsets.ime.getBottom(density).toDp() }
            val screenDp = LocalConfiguration.current.screenHeightDp.dp
            val chromeDp = 230.dp // header + footer + handle + paddings
            val bodyMax = (screenDp - imeDp - chromeDp).coerceAtLeast(140.dp)

            Column(
                modifier = Modifier
                    .heightIn(max = bodyMax)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                when (section) {
                    ConfigSection.ISO -> GeometricAxisEditor(
                        axis = draft.iso,
                        unitLabel = "ISO",
                        supportedHint = "Camera supports ${caps.sensitivityRange.lower} – ${caps.sensitivityRange.upper}",
                        accentColor = section.accent.color(),
                        resetKey = resetVersion,
                        presets = isoPresets(caps),
                        onAxisChange = { draft = draft.copy(iso = it) },
                        onPreset = { draft = draft.copy(iso = it); resetVersion++ },
                        formatValue = { it.roundToInt().toString() },
                        chipLabel = { it.roundToInt().toString() },
                    )

                    ConfigSection.SHUTTER -> GeometricAxisEditor(
                        axis = draft.exposure,
                        unitLabel = "ms",
                        supportedHint = "Camera supports ${trim(caps.exposureTimeRangeNs.lower / 1e6)} – " +
                            "${trim(caps.exposureTimeRangeNs.upper / 1e6)} ms",
                        accentColor = section.accent.color(),
                        resetKey = resetVersion,
                        presets = shutterPresets(caps),
                        onAxisChange = { draft = draft.copy(exposure = it) },
                        onPreset = { draft = draft.copy(exposure = it); resetVersion++ },
                        formatValue = { trim(it / 1e6) },
                        chipLabel = { shutterLabel(it.toLong()) },
                    )

                    ConfigSection.FOCUS -> FocusAxisEditor(
                        caps = caps,
                        focus = draft.focus,
                        accentColor = section.accent.color(),
                        resetKey = resetVersion,
                        onFocusChange = { draft = draft.copy(focus = it) },
                        onFocusGenerated = { draft = draft.copy(focus = it); resetVersion++ },
                    )

                    ConfigSection.FORMAT -> FormatEditor(
                        draft = draft,
                        accentColor = section.accent.color(),
                        freeBytes = rememberFreeBytes(),
                        onDraftChange = { draft = it },
                    )

                    ConfigSection.AVERAGE -> AverageEditor(
                        draft = draft,
                        accentColor = section.accent.color(),
                        onDraftChange = { draft = it },
                    )

                    ConfigSection.SETTLE -> SettleEditor(
                        draft = draft,
                        accentColor = section.accent.color(),
                        onDraftChange = { draft = it },
                    )
                }
            }

            SheetFooter(
                section = section,
                draft = draft,
                onApply = { onApply(draft) },
                onCancel = onCancel,
                onResetDefaults = { draft = SweepDefaults.forCamera(caps); resetVersion++ },
            )
        }
    }
}

@Composable
private fun SheetHeader(section: ConfigSection, draft: SweepConfig) {
    val tint = section.accent.color()
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(10.dp).clip(CircleShape).background(tint))
            Spacer(Modifier.width(10.dp))
            Text(
                section.title,
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.weight(1f),
            )
            val count = sectionCount(section, draft)
            Text(
                if (count != null) "$count values" else sectionDetail(section, draft),
                style = MaterialTheme.typography.labelLarge,
                color = tint,
            )
        }
        Text(
            section.description,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun SheetFooter(
    section: ConfigSection,
    draft: SweepConfig,
    onApply: () -> Unit,
    onCancel: () -> Unit,
    onResetDefaults: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            "${sectionImpact(section, draft)} → ${draft.totalFrames} frames · est. ${estimatedDuration(draft)}",
            style = MaterialTheme.typography.titleSmall,
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = onResetDefaults) { Text("Reset all") }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = onCancel) { Text("Cancel") }
                Button(onClick = onApply, enabled = draft.totalCaptures > 0) { Text("Apply") }
            }
        }
    }
}

private fun sectionImpact(section: ConfigSection, draft: SweepConfig): String = when (section) {
    ConfigSection.ISO -> "${draft.isoValues.size} ISO values"
    ConfigSection.SHUTTER -> "${draft.exposureValuesNs.size} shutter speeds"
    ConfigSection.FOCUS -> "${draft.focusValues.size} focus distances"
    ConfigSection.FORMAT -> draft.outputFormat.label
    ConfigSection.AVERAGE -> "× ${draft.framesToAverage} per configuration"
    ConfigSection.SETTLE -> "${draft.settleFrames} settle frames"
}

/** A +/- stepper: bounded small integers are faster to set this way than through a keyboard. */
@Composable
fun Stepper(
    value: Int,
    range: IntRange,
    accentColor: androidx.compose.ui.graphics.Color,
    onValueChange: (Int) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        FilledTonalIconButton(
            onClick = { onValueChange((value - 1).coerceIn(range)) },
            enabled = value > range.first,
        ) { Text("−", style = MaterialTheme.typography.titleLarge) }
        Text(
            value.toString(),
            style = MaterialTheme.typography.displaySmall,
            color = accentColor,
            textAlign = TextAlign.Center,
            modifier = Modifier.width(120.dp),
        )
        FilledTonalIconButton(
            onClick = { onValueChange((value + 1).coerceIn(range)) },
            enabled = value < range.last,
        ) { Text("+", style = MaterialTheme.typography.titleLarge) }
    }
}
