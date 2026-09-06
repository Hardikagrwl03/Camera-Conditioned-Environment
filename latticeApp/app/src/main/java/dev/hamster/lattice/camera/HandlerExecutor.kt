package dev.hamster.lattice.camera

import android.os.Handler
import java.util.concurrent.Executor

class HandlerExecutor(private val handler: Handler) : Executor {
    override fun execute(command: Runnable) {
        handler.post(command)
    }
}
