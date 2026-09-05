package dev.hamster.framesampler.ui

import android.content.Context
import android.hardware.camera2.CameraManager
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import androidx.compose.animation.Crossfade
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.viewmodel.compose.viewModel
import dev.hamster.framesampler.camera.CameraCapabilitiesReader
import dev.hamster.framesampler.camera.previewDisplayAspect
import dev.hamster.framesampler.model.SweepConfig

/**
 * Text drawn over the camera preview or a scrim stays light regardless of theme, because what sits
 * behind it is an arbitrary photograph rather than a themed surface.
 */
private val OnPreviewText = Color.White

@Composable
fun MainScreen(viewModel: MainViewModel = viewModel()) {
    val uiState by viewModel.uiState.collectAsState()

    Box(modifier = Modifier.fillMaxSize()) {
        Column(modifier = Modifier.fillMaxSize()) {
            // Preview: laid out at the sensor's on-screen aspect ratio and letterboxed on black,
            // so the operator sees exactly the frame that will be saved, undistorted. weight(fill
            // = false) lets it yield height to the controls when they need more (large font
            // scales) — it then shrinks and letterboxes at the sides, keeping the ratio intact.
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f, fill = false)
                    .background(Color.Black),
                contentAlignment = Alignment.Center,
            ) {
                CameraPreviewSurface(
                    onSurfaceAvailable = viewModel::onSurfaceAvailable,
                    onSurfaceDestroyed = viewModel::onSurfaceDestroyed,
                )

                val state = uiState
                if (state is UiState.Initializing) {
                    Text("Opening camera…", color = OnPreviewText)
                }
                if (state is UiState.Preview && state.warning != null) {
                    WarningBanner(state.warning, modifier = Modifier.align(Alignment.TopCenter))
                }
            }

            // Controls: themed surface filling the space the 3:4 preview leaves below. Scrollable
            // so a large font scale pushes content into a scroll rather than clipping the capture
            // button off screen.
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .windowInsetsPadding(WindowInsets.safeDrawing)
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                val state = uiState
                if (state is UiState.Preview) {
                    AttributeTabs(config = state.config, onSectionClick = viewModel::openSection)
                    CaptureButton(onClick = viewModel::startSweep)
                    TotalsPill(state.config)
                }
            }
        }

        when (val state = uiState) {
            is UiState.Capturing -> CapturingOverlay(state, onCancel = viewModel::cancelSweep)
            is UiState.Finished -> FinishedOverlay(state.summary, onDismiss = viewModel::backToPreview)
            is UiState.Error -> ErrorOverlay(state.message, onRetry = viewModel::retry)
            else -> Unit
        }

        val state = uiState
        val section = viewModel.editingSection
        if (state is UiState.Preview && section != null) {
            ConfigSheet(
                section = section,
                initialConfig = state.config,
                caps = state.caps,
                onApply = viewModel::applyConfig,
                onCancel = viewModel::closeSection,
            )
        }
    }
}

@Composable
private fun CameraPreviewSurface(
    onSurfaceAvailable: (Surface) -> Unit,
    onSurfaceDestroyed: () -> Unit,
) {
    val context = LocalContext.current
    // Read (cheap, static) characteristics up front so the SurfaceView can be laid out at the
    // preview's on-screen aspect ratio before the camera is opened.
    val previewCaps = remember {
        runCatching {
            val cameraManager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val id = CameraCapabilitiesReader.selectCameraId(cameraManager)
            CameraCapabilitiesReader.read(cameraManager, id)
        }.getOrNull()
    }
    val aspect = previewCaps?.previewDisplayAspect ?: (3f / 4f)

    AndroidView(
        // matchHeightConstraintsFirst: when the controls need more room than the screen leaves,
        // the preview must shrink *proportionally* (letterboxing at the sides) rather than being
        // squeezed to fit the leftover height, which would distort the image again.
        modifier = Modifier.aspectRatio(aspect, matchHeightConstraintsFirst = true),
        factory = { ctx ->
            SurfaceView(ctx).apply {
                holder.addCallback(object : SurfaceHolder.Callback {
                    override fun surfaceCreated(holder: SurfaceHolder) {
                        // The surface *buffer* stays in sensor coordinates: Camera2 requires a
                        // buffer size that matches a supported output size. Only the view's
                        // layout aspect is rotated for display.
                        previewCaps?.let { holder.setFixedSize(it.previewSize.width, it.previewSize.height) }
                        onSurfaceAvailable(holder.surface)
                    }

                    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {}

                    override fun surfaceDestroyed(holder: SurfaceHolder) {
                        onSurfaceDestroyed()
                    }
                })
            }
        },
    )
}

@Composable
private fun WarningBanner(warning: String, modifier: Modifier = Modifier) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        modifier = modifier
            .windowInsetsPadding(WindowInsets.statusBars)
            .padding(8.dp),
    ) {
        Text(
            warning,
            modifier = Modifier.padding(8.dp),
            color = MaterialTheme.colorScheme.onErrorContainer,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

/**
 * The seven sweep attributes as small tabs: the three sweep axes on the first row, the four
 * per-frame settings on the second. Two rows rather than a uniform grid because the control area
 * is only ~300dp tall once the 3:4 preview has taken its height.
 */
@Composable
private fun AttributeTabs(
    config: SweepConfig,
    onSectionClick: (ConfigSection) -> Unit,
    modifier: Modifier = Modifier,
) {
    val rows = listOf(ConfigSection.entries.take(3), ConfigSection.entries.drop(3))
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        rows.forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { section ->
                    AttributeTab(
                        section = section,
                        value = sectionValue(section, config),
                        onClick = onSectionClick,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

/** Label names the attribute, accent-coloured value names its state — scannable at a glance. */
@Composable
private fun AttributeTab(
    section: ConfigSection,
    value: String,
    onClick: (ConfigSection) -> Unit,
    modifier: Modifier = Modifier,
) {
    val tint = section.accent.color()
    Surface(
        onClick = { onClick(section) },
        shape = RoundedCornerShape(18.dp),
        color = section.accent.container(),
        modifier = modifier
            .heightIn(min = 58.dp)
            .semantics { contentDescription = "Edit ${section.title}" },
    ) {
        Column(
            modifier = Modifier
                .padding(horizontal = 10.dp, vertical = 8.dp)
                .animateContentSize(),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(6.dp).clip(CircleShape).background(tint))
                Spacer(Modifier.width(6.dp))
                Text(
                    section.shortLabel.uppercase(),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Crossfade(targetState = value, label = "tabValue") { v ->
                Text(
                    v,
                    style = MaterialTheme.typography.titleMedium,
                    color = tint,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

/** Totals, and the one place a warning belongs on this screen. */
@Composable
private fun TotalsPill(config: SweepConfig) {
    val empty = config.totalCaptures == 0
    Surface(
        shape = RoundedCornerShape(50),
        color = if (empty) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Text(
            if (empty) "No captures — an axis is empty" else "${config.totalFrames} frames · ${estimatedDuration(config)}",
            style = MaterialTheme.typography.labelLarge,
            color = if (empty) MaterialTheme.colorScheme.onErrorContainer else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 6.dp),
        )
    }
}

@Composable
private fun CaptureButton(
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    Box(
        modifier = modifier
            .size(76.dp)
            .clip(CircleShape)
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .border(3.dp, MaterialTheme.colorScheme.onSurface, CircleShape)
            .clickable(enabled = enabled, onClick = onClick)
            .semantics { contentDescription = "Start capture sweep" },
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .size(58.dp)
                .clip(CircleShape)
                .background(MaterialTheme.colorScheme.primary),
        )
    }
}

/** Full-screen scrim shared by the capturing, finished and error states. */
@Composable
private fun ScrimOverlay(content: @Composable ColumnScope.() -> Unit) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.scrim.copy(alpha = 0.72f)),
    ) {
        Column(
            modifier = Modifier
                .align(Alignment.Center)
                .windowInsetsPadding(WindowInsets.safeDrawing)
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            content = content,
        )
    }
}

@Composable
private fun CapturingOverlay(state: UiState.Capturing, onCancel: () -> Unit) {
    ScrimOverlay {
        Text(
            "Capturing ${state.done} / ${state.total}",
            color = OnPreviewText,
            style = MaterialTheme.typography.titleLarge,
        )
        LinearProgressIndicator(
            progress = { if (state.total > 0) state.done.toFloat() / state.total else 0f },
            modifier = Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 12.dp),
        )
        Text(
            "ISO ${state.currentIso} · ${"%.1f".format(state.currentExposureNs / 1_000_000.0)} ms · " +
                "${"%.2f".format(state.currentFocus)} D",
            color = OnPreviewText,
        )
        Text(
            state.sessionDirName,
            color = OnPreviewText.copy(alpha = 0.7f),
            style = MaterialTheme.typography.bodySmall,
        )
        Button(onClick = onCancel, modifier = Modifier.padding(top = 16.dp)) { Text("Cancel") }
    }
}

@Composable
private fun FinishedOverlay(summary: String, onDismiss: () -> Unit) {
    ScrimOverlay {
        Text("Sweep complete", color = OnPreviewText, style = MaterialTheme.typography.titleLarge)
        Text(
            summary,
            color = OnPreviewText,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 8.dp),
        )
        Button(onClick = onDismiss, modifier = Modifier.padding(top = 16.dp)) { Text("Done") }
    }
}

@Composable
private fun ErrorOverlay(message: String, onRetry: () -> Unit) {
    ScrimOverlay {
        Text(
            message,
            color = OnPreviewText,
            style = MaterialTheme.typography.bodyLarge,
            textAlign = TextAlign.Center,
        )
        Button(onClick = onRetry, modifier = Modifier.padding(top = 12.dp)) { Text("Retry") }
    }
}
