package org.cyaninc.switchboard

// Phone notifications without Google: listens to the user's ntfy topic (set up by the web UI through push.json) and
// shows what the machines' daemons publish there: a session needs you (Allow / Deny, or a reply), a session finished,
// a watch fired. Android requires a visible notification for a service that stays connected; it sits in a silent channel.
import android.app.*
import android.content.*
import android.os.*
import androidx.core.app.NotificationCompat
import androidx.core.app.RemoteInput
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ProcessLifecycleOwner
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

object Push {
    const val CH_SESSIONS = "sessions"
    const val CH_CONNECTION = "connection"
    // written by the web UI (lib.rs push_config)
    fun config(ctx: Context): JSONObject? = File(ctx.dataDir, "push.json").takeIf { it.exists() }?.let { JSONObject(it.readText()) }
    fun start(ctx: Context) {
        val c = config(ctx)
        if (c?.optString("server").isNullOrEmpty() && c?.optString("relay").isNullOrEmpty()) return
        ctx.startForegroundService(Intent(ctx, PushService::class.java))
    }
    fun channels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_SESSIONS, "Sessions", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "A session needs you, finished, or a watch fired" })
        nm.createNotificationChannel(NotificationChannel(CH_CONNECTION, "Connection", NotificationManager.IMPORTANCE_MIN).apply {
            description = "Shown while Switchboard listens for notifications (Android requires it); safe to hide" })
    }
    // the daemon this notice came from, with the token the app paired with
    fun machine(ctx: Context, url: String): JSONObject? {
        val ms = config(ctx)?.optJSONArray("machines") ?: return null
        return (0 until ms.length()).map { ms.getJSONObject(it) }.firstOrNull { it.optString("url").trimEnd('/') == url.trimEnd('/') }
    }
    fun post(ctx: Context, machineUrl: String, path: String, body: JSONObject): Boolean {
        val m = machine(ctx, machineUrl) ?: return false
        return try {
            val c = URL(m.getString("url").trimEnd('/') + path).openConnection() as HttpURLConnection
            c.requestMethod = "POST"; c.doOutput = true; c.connectTimeout = 10000; c.readTimeout = 15000
            c.setRequestProperty("Authorization", "Bearer " + m.getString("token"))
            c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(body.toString().toByteArray()) }
            val ok = c.responseCode in 200..299; c.disconnect(); ok
        } catch (e: Exception) { false }
    }
}

class PushService : Service() {
    @Volatile private var running = true
    private var worker: Thread? = null
    @Volatile private var current: HttpURLConnection? = null  // the open stream: dropped on a network change to reconnect at once
    private var watching = false
    private var updater: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Push.channels(this)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(this, Push.CH_CONNECTION).setSmallIcon(R.drawable.ic_notify)
            .setContentTitle("Listening for your sessions").setContentIntent(open).setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_MIN).build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, n, android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING)
        else startForeground(1, n)
        // no interrupt() to pick up a new config: a pending interrupt made the next notice's foreground check throw, and
        // that notice was dropped. listen() re-reads push.json on every line instead (ntfy sends a keepalive every 45s).
        if (worker?.isAlive != true) worker = thread(name = "push") { listen() }
        if (!watching) {  // Wi-Fi <-> mobile and the like: reconnect now, not after the stream's 5-minute timeout
            watching = true
            getSystemService(android.net.ConnectivityManager::class.java).registerDefaultNetworkCallback(object : android.net.ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: android.net.Network) { current?.disconnect() }
            })
        }
        if (updater?.isAlive != true) updater = thread(name = "update") {  // a newer app release: download it, say it's ready
            while (running) {
                try { Updater.check(this) } catch (e: Exception) { android.util.Log.w("Updater", "check failed", e) }
                try { Thread.sleep(Updater.EVERY) } catch (_: InterruptedException) {}
            }
        }
        return START_STICKY
    }

    override fun onDestroy() { running = false; worker?.interrupt(); super.onDestroy() }

    private fun listen() {
        val prefs = getSharedPreferences("push", MODE_PRIVATE)
        var wait = 2000L
        while (running) {
            val cfg = Push.config(this)
            val server = cfg?.optString("server").orEmpty(); val topic = cfg?.optString("topic").orEmpty()
            // signed in to an account: the relay's stream (one per account, the phone's own sign-in); else an ntfy topic
            val relay = cfg?.optString("relay").orEmpty(); val secret = cfg?.optString("secret").orEmpty()
            if (relay.isEmpty() && (server.isEmpty() || topic.isEmpty())) { stopSelf(); return }
            val lastKey = if (relay.isNotEmpty()) "last_relay" else "last"
            try {
                val since = prefs.getString(lastKey, null) ?: (if (relay.isNotEmpty()) "" else "all")  // a new sign-in: no replay
                val c = URL(if (relay.isNotEmpty()) "${relay.trimEnd('/')}/v1/push/json?since=$since" else "${server.trimEnd('/')}/$topic/json?since=$since").openConnection() as HttpURLConnection
                if (relay.isNotEmpty()) c.setRequestProperty("Authorization", "Bearer $secret")
                // the relay checks in every 4 min (fewer wake-ups), ntfy every 45 s; a silent stream past this is dead
                c.connectTimeout = 15000; c.readTimeout = if (relay.isNotEmpty()) 300000 else 90000
                current = c
                c.inputStream.bufferedReader().useLines { lines ->
                    wait = 2000L
                    for (line in lines) {
                        if (!running) break
                        val now = Push.config(this@PushService)
                        if (now?.optString("server") != server || now.optString("topic") != topic || now.optString("relay") != relay) break  // moved: reconnect
                        val ev = JSONObject(line)
                        if (ev.optString("event") != "message") continue
                        prefs.edit().putString(lastKey, ev.getString("id")).apply()
                        try { show(JSONObject(ev.getString("message")), now) } catch (e: Exception) { android.util.Log.w("PushService", "notice not shown", e) }
                    }
                }
            } catch (_: InterruptedException) {
            } catch (_: Exception) {
                try { Thread.sleep(wait) } catch (_: InterruptedException) {}
                wait = minOf(wait * 2, 60000L)
            }
        }
    }

    private fun inForeground(): Boolean {
        var fg = false
        val done = java.util.concurrent.CountDownLatch(1)
        Handler(Looper.getMainLooper()).post { fg = ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED); done.countDown() }
        done.await(); return fg
    }

    /** The chat on screen as "<machine url>|<entry>" (the web UI writes it through push_viewing), or null. */
    private fun viewing(): String? = File(dataDir, "viewing.txt").takeIf { it.exists() }?.readText()

    private fun show(note: JSONObject, cfg: JSONObject) {
        val kind = note.optString("kind")
        if (kind == "finished" && !cfg.optBoolean("finished", true)) return
        val id = (note.optString("machine") + note.optString("entry") + note.optString("request_id")).hashCode()
        val tag = note.optString("machine") + "|" + note.optString("entry")
        if (kind == "resolved") { getSystemService(NotificationManager::class.java).cancel(tag, id); return }  // answered elsewhere
        if (inForeground() && viewing() == note.optString("machine").trimEnd('/') + "|" + note.optString("entry")) return  // that chat is on screen: you're reading it
        val open = PendingIntent.getActivity(this, id, Intent(this, MainActivity::class.java)
            .putExtra("machine", note.optString("machine")).putExtra("entry", note.optString("entry"))
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        fun act(action: String, extra: Bundle.() -> Unit = {}) = PendingIntent.getBroadcast(this, (id.toString() + action).hashCode(),
            Intent(this, ActionReceiver::class.java).setAction(action).putExtras(Bundle().apply {
                putString("note", note.toString()); putInt("id", id); extra() }),
            PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val b = NotificationCompat.Builder(this, Push.CH_SESSIONS).setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(note.optString("title")).setContentText(note.optString("body"))
            .setStyle(NotificationCompat.BigTextStyle().bigText(note.optString("body")))
            .setContentIntent(open).setAutoCancel(true)
            .setCategory(if (kind == "finished" || kind == "inbox") NotificationCompat.CATEGORY_MESSAGE else NotificationCompat.CATEGORY_REMINDER)
        // the lock screen version, used when the phone hides sensitive content (Android's setting, not ours): who, not what
        b.setVisibility(NotificationCompat.VISIBILITY_PRIVATE).setPublicVersion(
                NotificationCompat.Builder(this, Push.CH_SESSIONS).setSmallIcon(R.drawable.ic_notify)
                    .setContentTitle(note.optString("title")).setContentText(
                        when (kind) { "tool" -> "Permission needed"; "question" -> "Claude has a question"; "plan" -> "A plan to approve"; else -> "Open Switchboard" }).build())
        val reply = RemoteInput.Builder("text").setLabel("Reply").build()
        when (kind) {
            "tool" -> { b.addAction(0, "Allow", act("allow")); b.addAction(0, "Deny", act("deny")) }
            "question", "finished" -> b.addAction(NotificationCompat.Action.Builder(0, "Reply", act("reply")).addRemoteInput(reply).build())
        }
        getSystemService(NotificationManager::class.java).notify(tag, id, b.build())
    }
}

class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val note = JSONObject(intent.getStringExtra("note") ?: return)
        val id = intent.getIntExtra("id", 0)
        val tag = note.optString("machine") + "|" + note.optString("entry")
        val text = RemoteInput.getResultsFromIntent(intent)?.getCharSequence("text")?.toString()
        val pending = goAsync()
        thread {
            val entry = note.optString("entry"); val machine = note.optString("machine")
            val ok = when (intent.action) {
                "allow", "deny" -> Push.post(ctx, machine, "/v1/entries/$entry/answer",
                    JSONObject().put("request_id", note.optString("request_id")).put("allow", intent.action == "allow"))
                "reply" -> if (note.optString("kind") == "question")
                    Push.post(ctx, machine, "/v1/entries/$entry/answer", JSONObject().put("request_id", note.optString("request_id"))
                        .put("allow", true).put("answers", JSONObject().put(note.optString("question"), text ?: "")))
                    else Push.post(ctx, machine, "/v1/entries/$entry/send", JSONObject().put("text", text ?: ""))
                else -> false
            }
            val nm = ctx.getSystemService(NotificationManager::class.java)
            if (ok) nm.cancel(tag, id)
            else nm.notify(tag, id, NotificationCompat.Builder(ctx, Push.CH_SESSIONS).setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(note.optString("title")).setContentText("Couldn't reach ${note.optString("name")}'s machine. Open Switchboard.")
                .setContentIntent(PendingIntent.getActivity(ctx, id, Intent(ctx, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE))
                .setAutoCancel(true).build())
            pending.finish()
        }
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) { if (intent.action == Intent.ACTION_BOOT_COMPLETED) Push.start(ctx) }
}
