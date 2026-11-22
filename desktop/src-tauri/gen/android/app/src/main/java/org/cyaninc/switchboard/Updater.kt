package org.cyaninc.switchboard

// The phone app updates itself from this repo's GitHub releases: PushService checks every few hours, downloads a newer
// Switchboard.apk, keeps it only if it's signed with the same key as this app, and says "Update ready". Tapping that
// opens the app (Android shows an install prompt only from the foreground), which hands it to Android's installer.
// The first such update asks to confirm; after that this app is its own installer and Android 12+ updates it silently.
import android.app.*
import android.content.*
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

object Updater {
    private const val CH = "updates"
    private const val LATEST = "https://api.github.com/repos/cyaninc-lab/switchboard/releases/latest"
    private const val NOTE_ID = 7
    const val EXTRA = "install_update"  // MainActivity: install this downloaded APK
    const val EVERY = 6 * 3600 * 1000L

    /** "0.1.31" > "0.1.30"? Unparseable is not newer. */
    fun newer(a: String, b: String): Boolean {
        val x = a.removePrefix("v").split(".").map { it.toIntOrNull() ?: return false }
        val y = b.split(".").map { it.toIntOrNull() ?: return false }
        for (i in 0 until maxOf(x.size, y.size)) { val d = x.getOrElse(i) { 0 } - y.getOrElse(i) { 0 }; if (d != 0) return d > 0 }
        return false
    }

    private fun get(url: String): HttpURLConnection = (URL(url).openConnection() as HttpURLConnection).apply {
        connectTimeout = 15000; readTimeout = 60000; setRequestProperty("User-Agent", "Switchboard")
    }

    /** Look for a newer release; when there is one, download it, check its signer, and say it's ready. */
    fun check(ctx: Context) {
        val rel = JSONObject(get(LATEST).inputStream.bufferedReader().readText())
        val tag = rel.getString("tag_name").removePrefix("v")
        val mine = ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: return
        if (!newer(tag, mine)) {  // up to date: a "ready" left over from before an update is gone too
            ctx.getSystemService(NotificationManager::class.java).cancel(NOTE_ID); return
        }
        val apk = File(ctx.cacheDir, "Switchboard-$tag.apk")
        if (!apk.exists()) {
            val assets = rel.getJSONArray("assets")
            val url = (0 until assets.length()).map { assets.getJSONObject(it) }
                .firstOrNull { it.getString("name") == "Switchboard.apk" }?.getString("browser_download_url") ?: return
            val tmp = File(ctx.cacheDir, "Switchboard-$tag.part")
            get(url).inputStream.use { i -> tmp.outputStream().use { i.copyTo(it) } }
            ctx.cacheDir.listFiles { f -> f.name.startsWith("Switchboard-") && f.name.endsWith(".apk") }?.forEach { it.delete() }
            tmp.renameTo(apk)
        }
        if (!sameSigner(ctx, apk)) { apk.delete(); return }  // not ours: never offer it
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH, "App updates", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "A new version of Switchboard is ready to install" })
        val open = PendingIntent.getActivity(ctx, NOTE_ID, Intent(ctx, MainActivity::class.java).putExtra(EXTRA, apk.path)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        nm.notify(NOTE_ID, NotificationCompat.Builder(ctx, CH).setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Switchboard $tag is ready").setContentText("Tap to install").setContentIntent(open)
            .setAutoCancel(true).setOnlyAlertOnce(true).build())
    }

    private fun signers(info: android.content.pm.PackageInfo?) =
        info?.signingInfo?.apkContentsSigners?.map { it.toByteArray().contentHashCode() }?.toSet().orEmpty()

    /** Only ever an upgrade: a stale or repeated request mustn't reinstall (or downgrade) the app. */
    private fun isNewer(ctx: Context, apk: File): Boolean {
        val theirs = ctx.packageManager.getPackageArchiveInfo(apk.path, 0)?.longVersionCode ?: return false
        return theirs > ctx.packageManager.getPackageInfo(ctx.packageName, 0).longVersionCode
    }

    fun sameSigner(ctx: Context, apk: File): Boolean {
        val pm = ctx.packageManager
        val theirs = pm.getPackageArchiveInfo(apk.path, PackageManager.GET_SIGNING_CERTIFICATES)
        val ours = pm.getPackageInfo(ctx.packageName, PackageManager.GET_SIGNING_CERTIFICATES)
        return theirs?.packageName == ctx.packageName && signers(theirs).isNotEmpty() && signers(theirs) == signers(ours)
    }

    /** From the foreground (MainActivity): hand the APK to Android's installer. */
    fun install(act: Activity, apk: File) {
        if (!apk.exists() || !sameSigner(act, apk) || !isNewer(act, apk)) return
        if (!act.packageManager.canRequestPackageInstalls()) {  // once: allow Switchboard to install updates
            act.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${act.packageName}")))
            return
        }
        act.getSystemService(NotificationManager::class.java).cancel(NOTE_ID)
        val pi = act.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(act.packageName)
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = pi.createSession(params)
        pi.openSession(id).use { s ->
            s.openWrite("Switchboard.apk", 0, apk.length()).use { out -> apk.inputStream().use { it.copyTo(out) }; s.fsync(out) }
            val status = PendingIntent.getBroadcast(act, id, Intent(act, UpdateReceiver::class.java),
                PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            s.commit(status.intentSender)
        }
    }
}

/** The installer's answer: it may need the user to confirm (the first time), or it failed. */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION") val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                ctx.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            PackageInstaller.STATUS_SUCCESS -> {}
            else -> android.util.Log.w("Updater", "update failed: " + intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE))
        }
    }
}
