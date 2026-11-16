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
fn clean(cmd: &mut Command) -> &mut Command {
    for (k, _) in std::env::vars() {
        if k.starts_with("CLAUDE") {
            cmd.env_remove(k);
        }
    }
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Embedded(Mutex::new(None)))
        .setup(|app| {
            if !daemon_alive() {
                // no background service on this machine: run our own daemon for as long as the app runs
                let child = clean(&mut Command::new(sidecar()))
                    .args(["serve", "--port", &PORT.to_string(), "--exit-with", &std::process::id().to_string()])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn();
                match child {
                    Ok(c) => *app.state::<Embedded>().0.lock().unwrap() = Some(c),
                    Err(e) => eprintln!("could not start the bundled daemon: {e}"),
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![local_daemon])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut c) = app.state::<Embedded>().0.lock().unwrap().take() {
                    let _ = c.kill();
                    let _ = c.wait();
                }
            }
        });
}
