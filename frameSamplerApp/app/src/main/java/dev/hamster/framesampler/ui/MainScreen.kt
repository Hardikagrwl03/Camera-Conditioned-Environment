package dev.hamster.framesampler.ui

import android.content.Context
import android.hardware.camera2.CameraManager
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.viewmodel.compose.viewModel
import dev.hamster.framesampler.camera.CameraCapabilitiesReader
import dev.hamster.framesampler.model.SweepConfig

@Composable
fun MainScreen(viewModel: MainViewModel = viewModel()) {
    val uiState by viewModel.uiState.collectAsState()

    Box(modifier = Modifier.fillMaxSize()) {
        CameraPreviewSurface(
            onSurfaceAvailable = viewModel::onSurfaceAvailable,
            onSurfaceDestroyed = viewModel::onSurfaceDestroyed,
        )

        when (val state = uiState) {
            is UiState.Initializing -> Box(modifier = Modifier.fillMaxSize()) {
                Text(
                    "Opening camera…",
                    modifier = Modifier.align(Alignment.Center),
                    color = Color.White,
                )
            }

            is UiState.Error -> Box(modifier = Modifier.fillMaxSize().padding(24.dp)) {
                Column(modifier = Modifier.align(Alignment.Center), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(state.message, color = Color.White, style = MaterialTheme.typography.bodyLarge)
                    Button(onClick = viewModel::retry, modifier = Modifier.padding(top = 12.dp)) { Text("Retry") }
                }
            }

            is UiState.Preview -> {
                TopBar(
                    config = state.config,
                    warning = state.warning,
                    onConfigureClick = viewModel::openConfigure,
                )
                CaptureButton(
                    modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 32.dp),
                    onClick = viewModel::startSweep,
                )
                if (viewModel.configureSheetVisible) {
                    ConfigureSheet(
                        initialConfig = state.config,
                        caps = state.caps,
                        onApply = viewModel::applyConfig,
                        onCancel = viewModel::cancelConfigure,
                    )
                }
            }

            is UiState.Capturing -> CapturingOverlay(state, onCancel = viewModel::cancelSweep)

            is UiState.Finished -> FinishedOverlay(state.summary, onDismiss = viewModel::backToPreview)
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
    // sensor's aspect ratio instead of being stretched to fill the screen.
    val previewCaps = remember {
        runCatching {
            val cameraManager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val id = CameraCapabilitiesReader.selectCameraId(cameraManager)
            CameraCapabilitiesReader.read(cameraManager, id)
        }.getOrNull()
    }
    val aspect = previewCaps?.let { it.previewSize.width.toFloat() / it.previewSize.height.toFloat() } ?: (9f / 16f)

    AndroidView(
        modifier = Modifier.fillMaxWidth().aspectRatio(aspect),
        factory = { ctx ->
            SurfaceView(ctx).apply {
                holder.addCallback(object : SurfaceHolder.Callback {
                    override fun surfaceCreated(holder: SurfaceHolder) {
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
private fun TopBar(config: SweepConfig, warning: String?, onConfigureClick: () -> Unit) {
    Column(modifier = Modifier.fillMaxWidth().padding(16.dp)) {
        androidx.compose.foundation.layout.Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(onClick = onConfigureClick, contentPadding = ButtonDefaults.TextButtonContentPadding) {
                Text("Configure")
            }
            AssistChip(
                onClick = {},
                label = {
                    Text(
                        "${config.isoValues.size} ISO × ${config.exposureValuesNs.size} exp × " +
                            "${config.focusValues.size} FD × ${config.framesToAverage} = ${config.totalFrames} frames",
                    )
                },
            )
        }
        if (warning != null) {
            Surface(color = MaterialTheme.colorScheme.errorContainer, modifier = Modifier.padding(top = 8.dp)) {
                Text(
                    warning,
                    modifier = Modifier.padding(8.dp),
                    color = MaterialTheme.colorScheme.onErrorContainer,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

@Composable
private fun CaptureButton(modifier: Modifier = Modifier, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        modifier = modifier.size(72.dp),
        shape = CircleShape,
        color = Color.White,
    ) {}
}

@Composable
private fun CapturingOverlay(state: UiState.Capturing, onCancel: () -> Unit) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.72f)),
    ) {
        Column(
            modifier = Modifier.align(Alignment.Center).padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("Capturing ${state.done} / ${state.total}", color = Color.White, style = MaterialTheme.typography.titleLarge)
            LinearProgressIndicator(
                progress = { if (state.total > 0) state.done.toFloat() / state.total else 0f },
                modifier = Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 12.dp),
            )
            Text(
                "ISO ${state.currentIso} · ${"%.1f".format(state.currentExposureNs / 1_000_000.0)} ms · " +
                    "${"%.2f".format(state.currentFocus)} D",
                color = Color.White,
            )
            Text(state.sessionDirName, color = Color.Gray, style = MaterialTheme.typography.bodySmall)
            Button(onClick = onCancel, modifier = Modifier.padding(top = 16.dp)) { Text("Cancel") }
        }
    }
}

@Composable
private fun FinishedOverlay(summary: String, onDismiss: () -> Unit) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.72f)),
    ) {
        Column(
            modifier = Modifier.align(Alignment.Center).padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("Sweep complete", color = Color.White, style = MaterialTheme.typography.titleLarge)
            Text(summary, color = Color.White, modifier = Modifier.padding(top = 8.dp))
            Button(onClick = onDismiss, modifier = Modifier.padding(top = 16.dp)) { Text("Done") }
        }
    }
}
