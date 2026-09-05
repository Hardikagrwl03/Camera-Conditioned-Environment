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
import dev.hamster.framesampler.storage.ConfigStore
import dev.hamster.framesampler.storage.SweepStorage
import dev.hamster.framesampler.ui.theme.Accent
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * One editable sweep attribute. Each is a tab on the preview screen with its own popup.
 *
 * [title] heads the popup, [shortLabel] labels the tab, [description] is the one-line explanation
 * shown in the popup header. The UI says "Shutter" while the data model and manifest keep
 * `exposure`/`exposureValuesNs`, so sessions already on disk and any analysis scripts stay readable.
 */
enum class ConfigSection(
    val title: String,
    val shortLabel: String,
    val description: String,
    val accent: Accent,
) {
    ISO("ISO sensitivity", "ISO", "Sensor gain applied to each frame.", Accent.ISO),
    SHUTTER("Shutter speed", "Shutter", "How long each frame is exposed.", Accent.SHUTTER),
    FOCUS("Focus distance", "Focus", "Where the lens is focused, in diopters.", Accent.FOCUS),
    FORMAT("Output format", "Format", "How each captured frame is encoded on disk.", Accent.FORMAT),
    AVERAGE("Frames to average", "Average", "Frames captured per configuration and averaged.", Accent.AVERAGE),
    SETTLE("Settle frames", "Settle", "Warm-up frames discarded after each settings change.", Accent.SETTLE),
}

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
    private val configStore = ConfigStore(application)

    private val _uiState = MutableStateFlow<UiState>(UiState.Initializing)
    val uiState: StateFlow<UiState> = _uiState.asStateFlow()

    /** The section whose editing overlay is open, or null when none is. */
    var editingSection by mutableStateOf<ConfigSection?>(null)
        private set

    private var previewSurface: Surface? = null
    private var sweepJob: Job? = null

    /**
     * The live sweep configuration, held here rather than only inside [UiState.Preview] so it
     * survives camera reinitialisation. The surface is destroyed and recreated on a theme change,
     * a backgrounding, or the screen going off, and each recreation reopens the camera — rebuilding
     * defaults there would silently discard whatever the operator had set up.
     */
    private var config: SweepConfig? = null

    /** Which camera [config] was built for; a different camera invalidates it. */
    private var configCameraId: String? = null

    /** Held so [applyConfig] can persist against the camera the config belongs to. */
    private var currentCaps: CameraCapabilities? = null

    fun onSurfaceAvailable(surface: Surface) {
        previewSurface = surface
        _uiState.value = UiState.Initializing
        viewModelScope.launch {
            try {
                val cameraId = CameraCapabilitiesReader.selectCameraId(cameraManager)
                val caps = controller.open(cameraId)
                controller.startPreview(surface)
                // Reuse the in-memory configuration across camera reinitialisation, then the
                // persisted one across app restarts, and only then fall back to defaults.
                val activeConfig = config?.takeIf { configCameraId == caps.cameraId }
                    ?: configStore.load(caps)
                    ?: SweepDefaults.forCamera(caps)
                config = activeConfig
                configCameraId = caps.cameraId
                currentCaps = caps
                val warning = if (!caps.supportsManualSensor) {
                    "This camera does not support manual sensor control; sweep values may be ignored by the device."
                } else null
                _uiState.value = UiState.Preview(activeConfig, caps, warning)
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

    fun openSection(section: ConfigSection) {
        editingSection = section
    }

    fun closeSection() {
        editingSection = null
    }

    /**
     * Each section's editor hands back a whole [SweepConfig] built from the live one with only its
     * own field replaced, so applying one section can never clobber another.
     */
    fun applyConfig(newConfig: SweepConfig) {
        config = newConfig
        currentCaps?.let { configStore.save(newConfig, it) }
        editingSection = null
        val state = _uiState.value as? UiState.Preview ?: return
        _uiState.value = state.copy(config = newConfig)
    }

    fun startSweep() {
        val state = _uiState.value as? UiState.Preview ?: return
        val config = state.config
        if (config.totalCaptures <= 0) {
            _uiState.value = state.copy(warning = "Configuration produces zero captures.")
            return
        }
        if (!storage.hasEnoughSpace(config.totalCaptures, config.outputFormat)) {
            _uiState.value = UiState.Error(
                "Not enough free space for ${config.totalCaptures} ${config.outputFormat.label} captures " +
                    "(~${storage.estimatedBytesNeeded(config.totalCaptures, config.outputFormat) / (1024 * 1024)} MB). " +
                    "Free up space or switch format.",
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
