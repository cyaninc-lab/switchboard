package org.cyaninc.switchboard

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.enableEdgeToEdge
import org.json.JSONObject
import java.io.File

class MainActivity : TauriActivity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    enableEdgeToEdge()
    super.onCreate(savedInstanceState)
    if (savedInstanceState == null) remember(intent)  // restored (or relaunched by Android): not a new tap
  }

  override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); remember(intent) }

  // a tapped notification names a session: leave it for the web UI to open (lib.rs push_take_open)
  private fun remember(intent: Intent?) {
    intent?.getStringExtra(Updater.EXTRA)?.let {  // "Update ready" was tapped: act on it once, never again on a relaunch
      intent.removeExtra(Updater.EXTRA); setIntent(intent)
      pendingUpdate = it; installUpdate()
    }
    val entry = intent?.getStringExtra("entry") ?: return
    File(dataDir, "open.json").writeText(JSONObject().put("machine", intent.getStringExtra("machine")).put("entry", entry).toString())
  }

  // the downloaded update to install; kept if Android first needs "allow installing" (back from that screen: go on)
  private var pendingUpdate: String? = null
  private fun installUpdate() {
    val p = pendingUpdate ?: return
    if (packageManager.canRequestPackageInstalls()) pendingUpdate = null
    Thread { Updater.install(this, File(p)) }.start()
  }

  override fun onResume() {
    super.onResume()
    if (pendingUpdate != null && packageManager.canRequestPackageInstalls()) installUpdate()
    // once notifications are set up: ask once to be left out of battery saving, or Android delays them for hours
    val prefs = getSharedPreferences("push", MODE_PRIVATE)
    if (Push.config(this) != null && !prefs.getBoolean("asked_battery", false)
        && !getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)) {
      prefs.edit().putBoolean("asked_battery", true).apply()
      startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
    }
  }

  // listening matters when the app isn't in front
  override fun onStop() { super.onStop(); Push.start(this) }
}
