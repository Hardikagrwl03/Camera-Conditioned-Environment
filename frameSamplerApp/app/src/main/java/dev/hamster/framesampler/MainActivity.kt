package dev.hamster.framesampler

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.Composable
import dev.hamster.framesampler.ui.MainScreen
import dev.hamster.framesampler.ui.PermissionGate
import dev.hamster.framesampler.ui.theme.FrameSamplerTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContent {
            FrameSamplerTheme {
                FrameSamplerApp()
            }
        }
    }
}

@Composable
private fun FrameSamplerApp() {
    // Full-bleed camera preview: no Scaffold chrome needed.
    PermissionGate {
        MainScreen()
    }
}
