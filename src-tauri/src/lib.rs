use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use tauri::ipc::CapabilityBuilder;
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;
use url::Url;

/// How long a graceful `/_desktop/shutdown` may take before the sidecar is
/// force-killed. The PyInstaller one-file bootloader needs this time to wait for
/// Python (and its Chromium children) and delete its `_MEI*` extraction folder.
const SERVER_EXIT_TIMEOUT: Duration = Duration::from_secs(5);

struct ServerState {
    port: u16,
    shutdown_token: String,
    child: Mutex<Option<CommandChild>>,
    /// Set once the sidecar process has terminated (its event channel closed).
    exited: Arc<AtomicBool>,
}

/// 256-bit token from the OS CSPRNG. The `/_desktop/*` routes skip the session
/// cookie and rely only on this value, so it must not be guessable by other
/// local processes (unlike a PID or start time).
fn generate_shutdown_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).expect("OS random number generator is unavailable");
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn reserve_loopback_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

fn wait_for_server(port: u16, exited: &AtomicBool) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(45);
    let address = format!("127.0.0.1:{port}");
    while Instant::now() < deadline {
        if exited.load(Ordering::SeqCst) {
            return Err("Riviu Reports server exited during startup.".to_string());
        }
        if TcpStream::connect_timeout(
            &address
                .parse::<SocketAddr>()
                .map_err(|error| error.to_string())?,
            Duration::from_millis(250),
        )
        .is_ok()
        {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(150));
    }
    Err("Riviu Reports server did not start within 45 seconds.".to_string())
}

fn start_server(app: &AppHandle, state: &ServerState) -> Result<(), String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    std::fs::create_dir_all(&data_dir).map_err(|error| error.to_string())?;

    let command = app
        .shell()
        .sidecar("riviu-server")
        .map_err(|error| error.to_string())?
        .env("RIVIU_PORT", state.port.to_string())
        .env("RIVIU_SHUTDOWN_TOKEN", &state.shutdown_token)
        .env("RIVIU_DATA_DIR", data_dir.to_string_lossy().to_string());
    let (mut receiver, child) = command.spawn().map_err(|error| error.to_string())?;
    // Drain the sidecar's events so stop_server can tell when the process (and
    // its inherited stdout/stderr handles) are really gone.
    let exited = state.exited.clone();
    exited.store(false, Ordering::SeqCst);
    tauri::async_runtime::spawn(async move {
        while let Some(event) = receiver.recv().await {
            if matches!(event, CommandEvent::Terminated(_)) {
                break;
            }
        }
        exited.store(true, Ordering::SeqCst);
    });
    if let Err(error) = wait_for_server(state.port, &state.exited) {
        let _ = child.kill();
        return Err(error);
    }
    *state.child.lock().map_err(|error| error.to_string())? = Some(child);
    Ok(())
}

#[tauri::command]
async fn check_for_update(app: AppHandle) -> Result<Option<String>, String> {
    let update = app
        .updater()
        .map_err(|error| error.to_string())?
        .check()
        .await
        .map_err(|error| error.to_string())?;
    Ok(update.map(|item| item.version.to_string()))
}

#[tauri::command]
async fn install_update(app: AppHandle) -> Result<(), String> {
    // On Windows the updater launches the NSIS installer and then calls
    // `std::process::exit(0)`, so RunEvent::Exit never fires. Stop the sidecar
    // in the pre-exit hook; otherwise riviu-server.exe (and Chromium) would be
    // orphaned and keep the executable locked while the installer replaces it.
    // This replaces the plugin's default hook, so keep its cleanup call too.
    let exit_handle = app.clone();
    let Some(update) = app
        .updater_builder()
        .on_before_exit(move || {
            stop_server(&exit_handle);
            exit_handle.cleanup_before_exit();
        })
        .build()
        .map_err(|error| error.to_string())?
        .check()
        .await
        .map_err(|error| error.to_string())?
    else {
        return Ok(());
    };

    let state = app.state::<ServerState>();
    let port = state.port;
    let token = state.shutdown_token.clone();
    let prepare_token = token.clone();
    tauri::async_runtime::spawn_blocking(move || {
        request_server_action(port, &prepare_token, "/_desktop/prepare-update")
    })
    .await
    .map_err(|error| error.to_string())??;

    let installation = update.download_and_install(|_, _| {}, || {}).await;
    if let Err(error) = installation {
        let _ = tauri::async_runtime::spawn_blocking(move || {
            request_server_action(port, &token, "/_desktop/cancel-update")
        })
        .await;
        return Err(error.to_string());
    }
    app.restart();
}

fn request_server_action(port: u16, shutdown_token: &str, path: &str) -> Result<(), String> {
    let address = format!("127.0.0.1:{port}");
    let socket_address = address
        .parse::<SocketAddr>()
        .map_err(|error| error.to_string())?;
    let mut stream = TcpStream::connect_timeout(&socket_address, Duration::from_secs(2))
        .map_err(|error| error.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .map_err(|error| error.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(3)))
        .map_err(|error| error.to_string())?;
    let request = format!(
        "POST {path} HTTP/1.1\r\nHost: {address}\r\nX-Riviu-Shutdown: {shutdown_token}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| error.to_string())?;
    let mut response = String::new();
    stream
        .take(8192)
        .read_to_string(&mut response)
        .map_err(|error| error.to_string())?;
    if response
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        == Some("200")
    {
        Ok(())
    } else {
        Err("Ứng dụng đang quét hoặc chưa sẵn sàng cập nhật. Sẽ thử lại sau.".to_string())
    }
}

/// Ask the sidecar to shut down, wait (bounded) for it to exit on its own, and
/// only force-kill it if it is still running. Idempotent: the child is taken
/// out of the state, so later calls (ExitRequested then Exit) return at once.
fn stop_server(app: &AppHandle) {
    let Some(state) = app.try_state::<ServerState>() else {
        return;
    };
    let child = state
        .child
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .take();
    let Some(child) = child else {
        return;
    };
    if request_server_action(state.port, &state.shutdown_token, "/_desktop/shutdown").is_ok() {
        let deadline = Instant::now() + SERVER_EXIT_TIMEOUT;
        while !state.exited.load(Ordering::SeqCst) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(100));
        }
    }
    if !state.exited.load(Ordering::SeqCst) {
        let _ = child.kill();
    }
}

pub fn run() {
    let port = reserve_loopback_port().expect("failed to reserve a local port");
    let shutdown_token = generate_shutdown_token();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(ServerState {
            port,
            shutdown_token,
            child: Mutex::new(None),
            exited: Arc::new(AtomicBool::new(false)),
        })
        .setup(|app| {
            let handle = app.handle();
            let state = handle.state::<ServerState>();
            start_server(&handle, &state).map_err(std::io::Error::other)?;
            let url = Url::parse(&format!("http://127.0.0.1:{}", state.port))
                .map_err(std::io::Error::other)?;
            // Grant only the two updater commands to this app's ephemeral origin.
            // The remote page never receives shell/plugin permissions.
            handle
                .add_capability(
                    CapabilityBuilder::new("loopback-updater")
                        .window("main")
                        .local(false)
                        .remote(format!("http://127.0.0.1:{}/*", state.port))
                        .permission("allow-check-for-update")
                        .permission("allow-install-update"),
                )
                .map_err(std::io::Error::other)?;
            app.get_webview_window("main")
                .ok_or_else(|| std::io::Error::other("main window is missing"))?
                .navigate(url)
                .map_err(std::io::Error::other)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![check_for_update, install_update])
        .build(tauri::generate_context!())
        .expect("error while building Riviu Reports");

    app.run(|app, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            stop_server(app);
        }
    });
}
