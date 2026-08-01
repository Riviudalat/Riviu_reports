use std::io::Write;
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;
use url::Url;

struct ServerState {
    port: u16,
    shutdown_token: String,
    child: Mutex<Option<CommandChild>>,
}

fn reserve_loopback_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

fn wait_for_server(port: u16) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(45);
    let address = format!("127.0.0.1:{port}");
    while Instant::now() < deadline {
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
    let (_receiver, child) = command.spawn().map_err(|error| error.to_string())?;
    if let Err(error) = wait_for_server(state.port) {
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
    let Some(update) = app
        .updater()
        .map_err(|error| error.to_string())?
        .check()
        .await
        .map_err(|error| error.to_string())?
    else {
        return Ok(());
    };

    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|error| error.to_string())?;
    app.restart();
}

fn request_server_shutdown(port: u16, shutdown_token: &str) {
    let address = format!("127.0.0.1:{port}");
    let Ok(socket_address) = address.parse::<SocketAddr>() else {
        return;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&socket_address, Duration::from_secs(2)) else {
        return;
    };
    let request = format!(
        "POST /_desktop/shutdown HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Riviu-Shutdown: {shutdown_token}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    let _ = stream.write_all(request.as_bytes());
}

fn stop_server(app: &AppHandle) {
    if let Some(state) = app.try_state::<ServerState>() {
        request_server_shutdown(state.port, &state.shutdown_token);
        if let Ok(mut child) = state.child.lock() {
            if let Some(child) = child.take() {
                thread::sleep(Duration::from_millis(300));
                let _ = child.kill();
            }
        }
    }
}

pub fn run() {
    let port = reserve_loopback_port().expect("failed to reserve a local port");
    let shutdown_token = format!(
        "{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("system clock before Unix epoch")
            .as_nanos()
    );
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(ServerState {
            port,
            shutdown_token,
            child: Mutex::new(None),
        })
        .setup(|app| {
            let handle = app.handle();
            let state = handle.state::<ServerState>();
            start_server(&handle, &state).map_err(std::io::Error::other)?;
            let url = Url::parse(&format!("http://127.0.0.1:{}", state.port))
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
