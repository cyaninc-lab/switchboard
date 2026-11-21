// Desktop shell (Linux, Windows; not the phone app): starts the bundled daemon when none is running on this machine, stops it on exit,
// and hands the UI a one-time pairing code for it. Everything else is the web UI talking HTTP.
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Manager, RunEvent};

const PORT: u16 = 7878;

struct Embedded(Mutex<Option<Child>>);

#[derive(serde::Serialize)]
struct LocalDaemon {
    url: String,
    code: String,
    embedded: bool,
}

fn daemon_alive() -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], PORT).into();
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

/// The sidecar sits next to the app's own executable (Tauri copies `externalBin` there).
fn sidecar() -> PathBuf {
    let exe = std::env::current_exe().expect("own path");
    let name = if cfg!(windows) { "switchboard-daemon.exe" } else { "switchboard-daemon" };
    exe.with_file_name(name)
}

/// The AppImage runs the app with its own libraries, GIO modules and search paths first (LD_LIBRARY_PATH,
/// GIO_MODULE_DIR, PATH and XDG_DATA_DIRS entries under $APPDIR, ...). Programs it starts inherit them and break: the
/// system's gio can't load ("undefined symbol"), so xdg-open fell back to launching browsers itself, and a link opened
/// in Firefox and then Chromium too. Children get the user's own environment back.
fn host_env(cmd: &mut Command) -> &mut Command {
    let Some(appdir) = std::env::var_os("APPDIR").map(|d| d.to_string_lossy().trim_end_matches('/').to_string()) else { return cmd };
    for k in ["LD_LIBRARY_PATH", "GIO_MODULE_DIR", "GDK_PIXBUF_MODULE_FILE", "GTK_PATH", "GTK_DATA_PREFIX", "GTK_EXE_PREFIX",
              "GTK_IM_MODULE_FILE", "GSETTINGS_SCHEMA_DIR", "PYTHONHOME", "PYTHONPATH", "PERLLIB", "QT_PLUGIN_PATH", "APPDIR", "APPIMAGE", "ARGV0", "OWD"] {
        cmd.env_remove(k);
    }
    for k in ["PATH", "XDG_DATA_DIRS"] {
        if let Ok(v) = std::env::var(k) {
            cmd.env(k, without_appdir(&v, &appdir));
        }
    }
    cmd
}

fn without_appdir(list: &str, appdir: &str) -> String {
    list.split(':').filter(|p| !p.is_empty() && !p.starts_with(appdir)).collect::<Vec<_>>().join(":")
}

/// Open a web or mail link in the user's browser / mail app: once, with xdg-open in the user's own environment on
/// Linux (the opener plugin tries a chain of launchers, and a launcher reporting failure after opening it opened it
/// twice), the system way elsewhere.
#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    if !(url.starts_with("http://") || url.starts_with("https://") || url.starts_with("mailto:")) {
        return Err("only web and mail links".into());
    }
    #[cfg(target_os = "linux")]
    {
        host_env(Command::new("xdg-open").arg(&url)).stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn().map(|_| ()).map_err(|e| e.to_string())
    }
    #[cfg(not(target_os = "linux"))]
    {
        tauri_plugin_opener::open_url(&url, None::<&str>).map_err(|e| e.to_string())
    }
}

/// The daemon must not inherit CLAUDE* vars (e.g. when the app is started from inside a Claude session), nor the
/// AppImage's environment (its sessions run the user's tools). On Windows it also must not flash a console window:
/// it's a console program started from a GUI app.
fn clean(cmd: &mut Command) -> &mut Command {
    host_env(cmd);
    for (k, _) in std::env::vars() {
        if k.starts_with("CLAUDE") {
            cmd.env_remove(k);
        }
    }
    #[cfg(windows)]
    std::os::windows::process::CommandExt::creation_flags(cmd, 0x0800_0000); // CREATE_NO_WINDOW
    cmd
}

#[tauri::command]
fn local_daemon(state: tauri::State<Embedded>) -> Result<LocalDaemon, String> {
    let deadline = Instant::now() + Duration::from_secs(15);
    while !daemon_alive() {
        if Instant::now() > deadline {
            return Err("The local daemon didn't start".into());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    // same binary, same default data dir as a background-service daemon, so the code is valid for either
    let out = clean(&mut Command::new(sidecar())).arg("pair").output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).into());
    }
    Ok(LocalDaemon {
        url: format!("http://127.0.0.1:{PORT}"),
        code: String::from_utf8_lossy(&out.stdout).trim().to_string(),
        embedded: state.0.lock().unwrap().is_some(),
    })
}

// ---- optional background service: keeps agents running while the app is closed ----
#[derive(serde::Serialize)]
struct ServiceStatus {
    supported: bool,
    installed: bool,
    active: bool,
    embedded: bool,
}

fn data_dir() -> PathBuf {
    let base = std::env::var_os("XDG_DATA_HOME").map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local/share"));
    base.join("switchboard")
}

fn unit_path() -> PathBuf {
    let base = std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config"));
    base.join("systemd/user/switchboard-daemon.service")
}

fn systemctl(args: &[&str]) -> Result<String, String> {
    let out = Command::new("systemctl").arg("--user").args(args).output().map_err(|e| e.to_string())?;
    if out.status.success() { Ok(String::from_utf8_lossy(&out.stdout).trim().into()) }
    else { Err(String::from_utf8_lossy(&out.stderr).trim().into()) }
}

fn which(cmd: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|p| std::env::split_paths(&p).map(|d| d.join(cmd)).find(|f| f.is_file()))
}

#[tauri::command]
fn service_status(state: tauri::State<Embedded>) -> ServiceStatus {
    let embedded = state.0.lock().unwrap().is_some();
    if !cfg!(target_os = "linux") {
        // ponytail: launchd (macOS) and a log-on task (Windows) come next; Linux/systemd first
        return ServiceStatus { supported: false, installed: false, active: false, embedded };
    }
    ServiceStatus {
        supported: true,
        installed: unit_path().exists(),
        active: systemctl(&["is-active", "switchboard-daemon"]).map(|s| s == "active").unwrap_or(false),
        embedded,
    }
}

// ---- file links in the chat: a path Claude wrote, on this machine ----
// Claude writes these paths, so "opening" must never run anything: folders open in the file manager, files of
// a harmless kind open in their default app, everything else (scripts, programs, unknown) is only shown in its folder.
const SAFE_EXT: &[&str] = &[
    "md", "markdown", "txt", "log", "csv", "tsv", "json", "jsonl", "yaml", "yml", "toml", "ini", "xml", "rst",
    "pdf", "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "mp3", "wav", "flac", "ogg", "mp4", "mkv", "webm", "mov",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp",
];

#[derive(Debug, PartialEq)]
enum Open { Folder, File, Reveal }

fn decide(path: &std::path::Path, is_dir: bool) -> Open {
    if is_dir { return Open::Folder; }
    let ext = path.extension().and_then(|e| e.to_str()).map(|e| e.to_ascii_lowercase()).unwrap_or_default();
    if SAFE_EXT.contains(&ext.as_str()) { Open::File } else { Open::Reveal }
}

/// Open a path from the chat (see above). Returns what it did: "opened" or "revealed".
#[tauri::command]
fn open_local(path: String) -> Result<String, String> {
    let p = match path.strip_prefix("~/").or_else(|| path.strip_prefix("~\\")) {
        Some(rest) => dirs_home().join(rest),
        None => PathBuf::from(&path),
    };
    let meta = std::fs::metadata(&p).map_err(|_| format!("No such file or folder: {}", p.display()))?;
    #[cfg(target_os = "linux")]
    if decide(&p, meta.is_dir()) != Open::Reveal {  // once, in the user's own environment (see open_url)
        return host_env(Command::new("xdg-open").arg(&p)).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null()).spawn().map(|_| "opened".into()).map_err(|e| e.to_string());
    }
    match decide(&p, meta.is_dir()) {
        Open::Folder | Open::File => tauri_plugin_opener::open_path(&p, None::<&str>).map(|_| "opened".into()),
        Open::Reveal => tauri_plugin_opener::reveal_item_in_dir(&p).map(|_| "revealed".into()),
    }
    .map_err(|e| e.to_string())
}

fn dirs_home() -> PathBuf {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    #[cfg(target_os = "linux")]
    fn programs_it_starts_get_the_users_environment_not_the_appimages() {
        assert_eq!(without_appdir("/tmp/.mount_X/usr/bin/:/usr/bin:/tmp/.mount_X/bin::/bin", "/tmp/.mount_X"), "/usr/bin:/bin");
        // as an AppImage sets it up (one test sets env: no other test reads these)
        unsafe {
            std::env::set_var("APPDIR", "/tmp/.mount_Test");
            std::env::set_var("LD_LIBRARY_PATH", "/tmp/.mount_Test/usr/lib/");
            std::env::set_var("GIO_MODULE_DIR", "/tmp/.mount_Test/usr/lib/gio/modules");
            std::env::set_var("XDG_DATA_DIRS", "/tmp/.mount_Test/usr/share/:/usr/local/share:/usr/share");
        }
        let out = host_env(Command::new("sh").args(["-c", "echo \"$LD_LIBRARY_PATH|$GIO_MODULE_DIR|$APPDIR|$XDG_DATA_DIRS\""]))
            .output().unwrap();
        assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "|||/usr/local/share:/usr/share");
        unsafe { for k in ["APPDIR", "LD_LIBRARY_PATH", "GIO_MODULE_DIR"] { std::env::remove_var(k); } }
    }
    #[test]
    fn service_copy_is_refreshed_only_when_it_differs() {
        let d = std::env::temp_dir().join(format!("sb-same-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let (a, b, c) = (d.join("a"), d.join("b"), d.join("c"));
        std::fs::write(&a, b"daemon v2").unwrap(); std::fs::write(&b, b"daemon v2").unwrap(); std::fs::write(&c, b"daemon v1").unwrap();
        assert!(same_contents(&a, &b));
        assert!(!same_contents(&a, &c));                  // same size, different bytes: an update
        assert!(!same_contents(&a, &d.join("missing")));  // the service has no copy yet
        assert!(same_contents(&d.join("missing"), &a));   // no bundled daemon (dev): leave the service alone
        std::fs::remove_dir_all(&d).unwrap();
    }
    #[test]
    fn opening_never_runs_anything() {
        assert_eq!(decide(Path::new("/x/notes.md"), false), Open::File);
        assert_eq!(decide(Path::new("C:/x/Shot.PNG"), false), Open::File);
        assert_eq!(decide(Path::new("/x/src"), true), Open::Folder);
        for p in ["/x/run.sh", "/x/a.exe", "/x/app.desktop", "/x/s.py", "/x/b.bat", "/x/c.ps1", "/x/Makefile", "/x/a.AppImage", "/x/page.html"] {
            assert_eq!(decide(Path::new(p), false), Open::Reveal, "{p}");
        }
    }
}

/// Copy the daemon somewhere stable (the app's own path changes with updates), write the unit, and hand over:
/// stop the app's own daemon, start the service. Same data dir, so the tree and tokens carry over.
#[tauri::command]
fn service_install(state: tauri::State<Embedded>) -> Result<(), String> {
    if !cfg!(target_os = "linux") { return Err("Background service is Linux-only for now".into()); }
    let bin_dir = data_dir().join("bin");
    std::fs::create_dir_all(&bin_dir).map_err(|e| e.to_string())?;
    let bin = bin_dir.join("switchboard-daemon");
    let tmp = bin_dir.join("switchboard-daemon.new");
    std::fs::copy(sidecar(), &tmp).map_err(|e| format!("copy daemon: {e}"))?;
    std::fs::rename(&tmp, &bin).map_err(|e| e.to_string())?;  // atomic swap, safe while an old copy runs
    let claude = which("claude").map(|p| format!("Environment=SWITCHBOARD_CLAUDE={}\n", p.display())).unwrap_or_default();
    let path = std::env::var("PATH").unwrap_or_default();
    let unit = format!(
        "[Unit]\nDescription=Switchboard daemon (keeps Claude agents running)\n\n[Service]\nExecStart={} serve --port {PORT}\nRestart=on-failure\nEnvironment=PATH={path}\n{claude}\n[Install]\nWantedBy=default.target\n",
        bin.display());
    std::fs::create_dir_all(unit_path().parent().unwrap()).map_err(|e| e.to_string())?;
    std::fs::write(unit_path(), unit).map_err(|e| e.to_string())?;
    systemctl(&["daemon-reload"])?;
    stop_embedded(&state);
    systemctl(&["enable", "--now", "switchboard-daemon"])?;
    wait_alive()
}

/// Stop and remove the service, then go back to running the app's own daemon.
#[tauri::command]
fn service_uninstall(state: tauri::State<Embedded>) -> Result<(), String> {
    if unit_path().exists() {
        let _ = systemctl(&["disable", "--now", "switchboard-daemon"]);
        std::fs::remove_file(unit_path()).map_err(|e| e.to_string())?;
        let _ = systemctl(&["daemon-reload"]);
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while daemon_alive() && Instant::now() < deadline { std::thread::sleep(Duration::from_millis(200)); }
    start_embedded(&state);
    wait_alive()
}

/// "Started" isn't "listening": wait until the daemon answers so the UI reconnects on its first try.
fn wait_alive() -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(15);
    while !daemon_alive() {
        if Instant::now() > deadline { return Err("The daemon didn't come up".into()); }
        std::thread::sleep(Duration::from_millis(200));
    }
    Ok(())
}

/// The one-file daemon is two processes (launcher + real daemon) in their own process group: stop the group.
fn stop_embedded(state: &Embedded) {
    if let Some(mut c) = state.0.lock().unwrap().take() {
        #[cfg(unix)]
        {
            // "--" so the negative number is read as a process group, not a signal option
            let group = format!("-{}", c.id());
            let _ = Command::new("kill").args(["-TERM", "--", &group]).status();
            let deadline = Instant::now() + Duration::from_secs(3);
            while Command::new("kill").args(["-0", "--", &group]).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false)
                && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(100));
            }
            let _ = Command::new("kill").args(["-KILL", "--", &group]).stderr(Stdio::null()).status();
        }
        #[cfg(windows)]
        {
            // the launcher and the real daemon: stop the whole tree
            let mut tk = Command::new("taskkill");
            tk.args(["/T", "/F", "/PID", &c.id().to_string()]).stdout(Stdio::null()).stderr(Stdio::null());
            std::os::windows::process::CommandExt::creation_flags(&mut tk, 0x0800_0000);
            let _ = tk.status();
        }
        let _ = c.kill();
        let _ = c.wait();
        let deadline = Instant::now() + Duration::from_secs(5);
        while daemon_alive() && Instant::now() < deadline { std::thread::sleep(Duration::from_millis(200)); }
    }
}

/// The background service runs its own copy of the daemon (`service_install`), so an app update never reached it: the
/// service kept the old daemon. At start, a release build whose bundled daemon differs from the service's copy puts the
/// new one in and restarts the service (live sessions resume on their next message), then waits for it to answer.
fn refresh_service() {
    if !cfg!(target_os = "linux") || cfg!(debug_assertions) || !unit_path().exists() { return; }
    let bin = data_dir().join("bin").join("switchboard-daemon");
    if same_contents(&sidecar(), &bin) { return; }
    let tmp = bin.with_extension("new");
    if std::fs::copy(sidecar(), &tmp).and_then(|_| std::fs::rename(&tmp, &bin)).is_err() { return; }
    if systemctl(&["restart", "switchboard-daemon"]).is_ok() { let _ = wait_alive(); }
}

fn same_contents(a: &std::path::Path, b: &std::path::Path) -> bool {
    match (std::fs::metadata(a), std::fs::metadata(b)) {
        (Ok(x), Ok(y)) if x.len() == y.len() => std::fs::read(a).ok() == std::fs::read(b).ok(),
        (Err(_), _) => true,  // no bundled daemon (a dev run): nothing to hand over
        _ => false,
    }
}

fn start_embedded(state: &Embedded) {
    if daemon_alive() { return; }
    let mut cmd = Command::new(sidecar());
    #[cfg(unix)]
    std::os::unix::process::CommandExt::process_group(&mut cmd, 0);
    let child = clean(&mut cmd)
        .args(["serve", "--port", &PORT.to_string(), "--exit-with", &std::process::id().to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
    match child {
        Ok(c) => *state.0.lock().unwrap() = Some(c),
        Err(e) => eprintln!("could not start the bundled daemon: {e}"),
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Embedded(Mutex::new(None)))
        .setup(|app| {
            refresh_service();  // an update: the background service gets this version's daemon too
            // no daemon answering (no background service): run our own for as long as the app runs
            start_embedded(&app.state::<Embedded>());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![local_daemon, service_status, service_install, service_uninstall, open_local, open_url])
        // popped-out session windows belong to the main one: closing it closes the app, not just that window
        .on_window_event(|w, e| {
            if w.label() == "main" && matches!(e, tauri::WindowEvent::Destroyed) {
                w.app_handle().exit(0);
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_embedded(&app.state::<Embedded>());
            }
        });
}
