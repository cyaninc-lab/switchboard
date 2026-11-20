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
        .run(tauri::generate_context!())
        .expect("error while running the phone app");
}
