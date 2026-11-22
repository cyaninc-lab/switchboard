// The app's entry point. The desktop app (desktop.rs) runs its own daemon, background service, updater and windows;
// the phone app only connects to machines' daemons, so it is the web UI plus a few phone plugins.
#[cfg(desktop)]
mod desktop;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(desktop)]
    desktop::run();
    #[cfg(mobile)]
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_barcode_scanner::init())  // scanning a machine's pairing QR code
        .invoke_handler(tauri::generate_handler![phone::push_config, phone::push_take_open, phone::push_viewing, phone::push_status])
        .run(tauri::generate_context!())
        .expect("error while running the phone app");
}

// The phone app's notifications are shown by PushService.kt, which runs without the web UI: the UI hands it the push
// server, its topic and the paired machines (with their tokens, in the app's private folder) through push.json.
#[cfg(mobile)]
mod phone {
    use tauri::Manager;

    fn file(app: &tauri::AppHandle, name: &str) -> Result<std::path::PathBuf, String> {
        Ok(app.path().app_data_dir().map_err(|e| e.to_string())?.join(name))  // = Android's dataDir, as the Kotlin side reads
    }

    #[tauri::command]
    pub fn push_config(app: tauri::AppHandle, config: String) -> Result<(), String> {
        let p = file(&app, "push.json")?;
        std::fs::create_dir_all(p.parent().unwrap()).map_err(|e| e.to_string())?;
        std::fs::write(p, config).map_err(|e| e.to_string())
    }

    /// The session open on screen ("<machine url>|<entry id>"), or None: PushService.kt skips notices for it while
    /// the app is in front (you're reading it), and shows the rest.
    #[tauri::command]
    pub fn push_viewing(app: tauri::AppHandle, view: Option<String>) -> Result<(), String> {
        let p = file(&app, "viewing.txt")?;
        match view {
            Some(v) => {
                std::fs::create_dir_all(p.parent().unwrap()).map_err(|e| e.to_string())?;
                std::fs::write(p, v).map_err(|e| e.to_string())
            }
            None => { let _ = std::fs::remove_file(p); Ok(()) }
        }
    }

    /// The notification listener's state (PushService.kt writes it): {"state": "connected" | "reconnecting", "since", "detail"}.
    #[tauri::command]
    pub fn push_status(app: tauri::AppHandle) -> Option<String> {
        std::fs::read_to_string(file(&app, "push-status.json").ok()?).ok()
    }

    /// The session a tapped notification named, once (MainActivity.kt leaves it in open.json).
    #[tauri::command]
    pub fn push_take_open(app: tauri::AppHandle) -> Option<String> {
        let p = file(&app, "open.json").ok()?;
        let s = std::fs::read_to_string(&p).ok()?;
        let _ = std::fs::remove_file(p);
        Some(s)
    }
}
