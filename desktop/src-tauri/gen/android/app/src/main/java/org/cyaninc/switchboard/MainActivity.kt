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
    remember(intent)
  }

  override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); remember(intent) }

  // a tapped notification names a session: leave it for the web UI to open (lib.rs push_take_open)
  private fun remember(intent: Intent?) {
    val entry = intent?.getStringExtra("entry") ?: return
    File(dataDir, "open.json").writeText(JSONObject().put("machine", intent.getStringExtra("machine")).put("entry", entry).toString())
  }

  override fun onResume() {
    super.onResume()
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
