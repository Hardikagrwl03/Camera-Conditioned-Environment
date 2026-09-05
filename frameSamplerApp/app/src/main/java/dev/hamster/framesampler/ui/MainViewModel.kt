package dev.hamster.framesampler.ui

import android.app.Application
import android.content.Context
import android.hardware.camera2.CameraManager
import android.view.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.hamster.framesampler.camera.CameraCapabilities
import dev.hamster.framesampler.camera.CameraCapabilitiesReader
import dev.hamster.framesampler.camera.CameraController
import dev.hamster.framesampler.model.SweepConfig
import dev.hamster.framesampler.model.SweepDefaults
import dev.hamster.framesampler.storage.CaptureRecord
import dev.hamster.framesampler.storage.SweepStorage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed interface UiState {
    data object Initializing : UiState
    data class Preview(val config: SweepConfig, val caps: CameraCapabilities, val warning: String? = null) : UiState
    data class Capturing(
        val done: Int,
        val total: Int,
        val currentIso: Int,
        val currentExposureNs: Long,
        val currentFocus: Float,
        val sessionDirName: String,
    ) : UiState
    data class Finished(val config: SweepConfig, val caps: CameraCapabilities, val summary: String) : UiState
    data class Error(val message: String) : UiState
}

class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val cameraManager = application.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    private val controller = CameraController(cameraManager)
    private val storage = SweepStorage()

    private val _uiState = MutableStateFlow<UiState>(UiState.Initializing)
    val uiState: StateFlow<UiState> = _uiState.asStateFlow()

    var configureSheetVisible by mutableStateOf(false)
        private set

    private var previewSurface: Surface? = null
    private var sweepJob: Job? = null

    fun onSurfaceAvailable(surface: Surface) {
        previewSurface = surface
        _uiState.value = UiState.Initializing
        viewModelScope.launch {
            try {
                val cameraId = CameraCapabilitiesReader.selectCameraId(cameraManager)
                val caps = controller.open(cameraId)
                controller.startPreview(surface)
                val defaultConfig = SweepDefaults.forCamera(caps)
                val warning = if (!caps.supportsManualSensor) {
                    "This camera does not support manual sensor control; sweep values may be ignored by the device."
                } else null
                _uiState.value = UiState.Preview(defaultConfig, caps, warning)
            } catch (e: Exception) {
                _uiState.value = UiState.Error(e.message ?: "Failed to open camera")
            }
        }
    }

    fun onSurfaceDestroyed() {
        sweepJob?.cancel()
        previewSurface = null
        controller.close()
        _uiState.value = UiState.Initializing
    }

    fun retry() {
        previewSurface?.let { onSurfaceAvailable(it) }
    }

    fun openConfigure() {
        configureSheetVisible = true
    }

    fun cancelConfigure() {
        configureSheetVisible = false
    }

    fun applyConfig(newConfig: SweepConfig) {
        val state = _uiState.value as? UiState.Preview ?: return
        _uiState.value = state.copy(config = newConfig)
        configureSheetVisible = false
    }

    fun defaultsForCurrentCamera(): SweepConfig? {
        val state = _uiState.value as? UiState.Preview ?: return null
        return SweepDefaults.forCamera(state.caps)
    }

    fun startSweep() {
        val state = _uiState.value as? UiState.Preview ?: return
        val config = state.config
        if (config.totalCaptures <= 0) {
            _uiState.value = state.copy(warning = "Configuration produces zero captures.")
            return
        }
        if (!storage.hasEnoughSpace(config.totalCaptures)) {
            _uiState.value = UiState.Error(
                "Not enough free space for an estimated ${config.totalCaptures} captures. Free up space and try again.",
            )
            return
        }

        val sessionDir = storage.createSessionDir()
        sweepJob = viewModelScope.launch {
            _uiState.value = UiState.Capturing(0, config.totalCaptures, config.isoValues.first(), config.exposureValuesNs.first(), config.focusValues.first(), sessionDir.name)
            // Accumulated from onCaptureWritten as captures land, independent of the source list
            // controller.runSweep() would otherwise only return on normal completion — this way a
            // manifest/CSV documenting what *did* get captured survives a cancellation (e.g. the
            // app backgrounded mid-sweep) or a mid-sweep error, not just a full run.
            val records = mutableListOf<CaptureRecord>()
            var cancelled = false

            fun writeManifestForWhateverWasCaptured() {
                if (records.isNotEmpty()) {
                    storage.writeManifest(sessionDir, state.caps, config, records, appVersionName())
                    storage.scanSessionDir(getApplication(), sessionDir)
                }
            }

            try {
                controller.runSweep(
                    config = config,
                    onProgress = { progress ->
                        _uiState.value = UiState.Capturing(
                            progress.done,
                            progress.total,
                            progress.currentIso,
                            progress.currentExposureNs,
                            progress.currentFocus,
                            sessionDir.name,
                        )
                    },
                    onCaptureWritten = { record, bytes ->
                        storage.writeJpeg(sessionDir, record, bytes)
                        records += record
                    },
                )
                writeManifestForWhateverWasCaptured()
                val unsettled = records.count { !it.settled }
                val summary = buildString {
                    append("Captured ${records.size} frames to ${sessionDir.name}.")
                    if (unsettled > 0) append(" $unsettled of ${records.size} did not settle to the requested values — see manifest.json.")
                }
                _uiState.value = UiState.Finished(config, state.caps, summary)
            } catch (e: CancellationException) {
                cancelled = true
                writeManifestForWhateverWasCaptured()
                throw e
            } catch (e: Exception) {
                writeManifestForWhateverWasCaptured()
                _uiState.value = UiState.Error(e.message ?: "Sweep failed")
            } finally {
                if (cancelled) {
                    _uiState.value = UiState.Preview(config, state.caps)
                }
            }
        }
    }

    fun cancelSweep() {
        sweepJob?.cancel()
    }

    /** Spec item 9: after a sweep finishes, return to the initial preview screen. */
    fun backToPreview() {
        val state = _uiState.value
        if (state is UiState.Finished) {
            _uiState.value = UiState.Preview(state.config, state.caps)
        }
    }

    private fun appVersionName(): String = try {
        val ctx = getApplication<Application>()
        ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "unknown"
    } catch (e: Exception) {
        "unknown"
    }

    override fun onCleared() {
        sweepJob?.cancel()
        controller.shutdown()
    }
}
