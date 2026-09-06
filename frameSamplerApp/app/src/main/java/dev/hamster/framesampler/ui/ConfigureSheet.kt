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
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.text.style.TextOverflow
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
 * Edits apply live: [onConfigChange] fires on every change and the caller commits it immediately,
 * so closing the sheet — by the handle, the scrim or Back — is all that is needed. There is no
 * Apply or Cancel, and correspondingly no draft to get out of sync with the live configuration.
 * Each editor hands back a whole config with only its own field replaced, so one section can never
 * clobber another.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConfigSheet(
    section: ConfigSection,
    config: SweepConfig,
    caps: CameraCapabilities,
    onConfigChange: (SweepConfig) -> Unit,
    onDismiss: () -> Unit,
) {
    // Only re-seeds the text field buffers on a programmatic replacement (Reset), never on typing.
    var resetVersion by remember(section) { mutableStateOf(0) }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
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
            SheetHeader(section, config)

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
                        axis = config.iso,
                        unitLabel = "ISO",
                        supportedHint = "Camera supports ${caps.sensitivityRange.lower} – ${caps.sensitivityRange.upper}",
                        accentColor = section.accent.color(),
                        resetKey = resetVersion,
                        presets = isoPresets(caps),
                        onAxisChange = { onConfigChange(config.copy(iso = it)) },
                        onPreset = { onConfigChange(config.copy(iso = it)); resetVersion++ },
                        formatValue = { it.roundToInt().toString() },
                        chipLabel = { it.roundToInt().toString() },
                    )

                    ConfigSection.SHUTTER -> GeometricAxisEditor(
                        axis = config.exposure,
                        unitLabel = "ms",
                        supportedHint = "Camera supports ${trim(caps.exposureTimeRangeNs.lower / 1e6)} – " +
                            "${trim(caps.exposureTimeRangeNs.upper / 1e6)} ms",
                        accentColor = section.accent.color(),
                        resetKey = resetVersion,
                        presets = shutterPresets(caps),
                        onAxisChange = { onConfigChange(config.copy(exposure = it)) },
                        onPreset = { onConfigChange(config.copy(exposure = it)); resetVersion++ },
                        formatValue = { trim(it / 1e6) },
                        chipLabel = { shutterLabel(it.toLong()) },
                    )

                    ConfigSection.FOCUS -> FocusAxisEditor(
                        caps = caps,
                        focus = config.focus,
                        accentColor = section.accent.color(),
                        resetKey = resetVersion,
                        onFocusChange = { onConfigChange(config.copy(focus = it)) },
                        onPreset = { onConfigChange(config.copy(focus = it)); resetVersion++ },
                    )

                    ConfigSection.FORMAT -> FormatEditor(
                        draft = config,
                        accentColor = section.accent.color(),
                        freeBytes = rememberFreeBytes(),
                        onDraftChange = onConfigChange,
                    )

                    ConfigSection.AVERAGE -> AverageEditor(
                        draft = config,
                        accentColor = section.accent.color(),
                        onDraftChange = onConfigChange,
                    )

                    ConfigSection.SETTLE -> SettleEditor(
                        draft = config,
                        accentColor = section.accent.color(),
                        onDraftChange = onConfigChange,
                    )

                    ConfigSection.DOWNSCALE -> {
                        val full = if (config.outputFormat == OutputFormat.PNG) caps.largestYuvSize else caps.largestJpegSize
                        DownscaleEditor(
                            draft = config,
                            accentColor = section.accent.color(),
                            fullWidth = full.width,
                            fullHeight = full.height,
                            onDraftChange = onConfigChange,
                        )
                    }
                }
            }

            SheetFooter(
                section = section,
                config = config,
                onReset = {
                    onConfigChange(resetSection(section, config, caps))
                    resetVersion++
                },
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
    config: SweepConfig,
    onReset: () -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            "${sectionImpact(section, config)} → ${config.totalFrames} frames · est. ${estimatedDuration(config)}",
            style = MaterialTheme.typography.titleSmall,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        Spacer(Modifier.width(12.dp))
        // Outlined rather than a text button: the edit it performs is immediate and there is no
        // Cancel to undo it, so it should read unmistakably as a button rather than as a link.
        OutlinedButton(onClick = onReset) { Text("Reset") }
    }
}

/**
 * Resets only the section being edited. Scoped rather than global because the edit takes effect
 * immediately and there is no Cancel to undo it — a single button that silently wiped all seven
 * settings would be unrecoverable.
 */
private fun resetSection(
    section: ConfigSection,
    config: SweepConfig,
    caps: CameraCapabilities,
): SweepConfig {
    val defaults = SweepDefaults.forCamera(caps)
    return when (section) {
        ConfigSection.ISO -> config.copy(iso = defaults.iso)
        ConfigSection.SHUTTER -> config.copy(exposure = defaults.exposure)
        ConfigSection.FOCUS -> config.copy(focus = defaults.focus)
        ConfigSection.FORMAT -> config.copy(outputFormat = defaults.outputFormat)
        ConfigSection.AVERAGE -> config.copy(framesToAverage = defaults.framesToAverage)
        ConfigSection.SETTLE -> config.copy(settleFrames = defaults.settleFrames)
        ConfigSection.DOWNSCALE -> config.copy(downscale = defaults.downscale)
    }
}

private fun sectionImpact(section: ConfigSection, draft: SweepConfig): String = when (section) {
    ConfigSection.ISO -> "${draft.isoValues.size} ISO values"
    ConfigSection.SHUTTER -> "${draft.exposureValuesNs.size} shutter speeds"
    ConfigSection.FOCUS -> "${draft.focusValues.size} focus distances"
    ConfigSection.FORMAT -> draft.outputFormat.label
    ConfigSection.AVERAGE -> "× ${draft.framesToAverage} per configuration"
    ConfigSection.SETTLE -> "${draft.settleFrames} settle frames"
    ConfigSection.DOWNSCALE -> if (draft.downscale == 1) "full resolution" else "${draft.downscale}x downscale"
}

/**
 * A +/- stepper: bounded small integers are faster to set this way than through a keyboard.
 *
 * [compact] shrinks it to sit beside a label instead of owning the row. Pass a non-filling
 * [modifier] with it: the default fills the width, which inside a Row leaves nothing for a
 * sibling weighted label and collapses it to one character per line.
 */
@Composable
fun Stepper(
    value: Int,
    range: IntRange,
    accentColor: androidx.compose.ui.graphics.Color,
    onValueChange: (Int) -> Unit,
    modifier: Modifier = Modifier.fillMaxWidth(),
    compact: Boolean = false,
) {
    Row(
        modifier = modifier,
        horizontalArrangement = if (compact) Arrangement.End else Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        FilledTonalIconButton(
            onClick = { onValueChange((value - 1).coerceIn(range)) },
            enabled = value > range.first,
        ) { Text("−", style = MaterialTheme.typography.titleLarge) }
        Text(
            value.toString(),
            style = if (compact) MaterialTheme.typography.titleLarge else MaterialTheme.typography.displaySmall,
            color = accentColor,
            textAlign = TextAlign.Center,
            modifier = Modifier.width(if (compact) 48.dp else 120.dp),
        )
        FilledTonalIconButton(
            onClick = { onValueChange((value + 1).coerceIn(range)) },
            enabled = value < range.last,
        ) { Text("+", style = MaterialTheme.typography.titleLarge) }
    }
}
