// Desktop shell: starts the bundled daemon when none is running on this machine, stops it on exit,
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

/// The daemon must not inherit CLAUDE* vars (e.g. when the app is started from inside a Claude session).
/// On Windows it also must not flash a console window: it's a console program started from a GUI app.
fn clean(cmd: &mut Command) -> &mut Command {
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Embedded(Mutex::new(None)))
        .setup(|app| {
            // no daemon answering (no background service): run our own for as long as the app runs
            start_embedded(&app.state::<Embedded>());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![local_daemon, service_status, service_install, service_uninstall])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_embedded(&app.state::<Embedded>());
            }
        });
}
