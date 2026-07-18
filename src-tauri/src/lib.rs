//! finds-you-jobs Tauri shell — rarely-touched glue (architecture §4.1).
//! Spawns + supervises the Python sidecar, exposes the PORT/TOKEN handshake to
//! the frontend as Tauri commands, and kills the sidecar's process group on
//! quit. All the intelligence is in the sidecar; this is process management +
//! window lifecycle only.

mod sidecar;

use std::thread;

use tauri::{Manager, RunEvent, State};

use sidecar::{dev_cwd, spawn_once, supervise, AppState};

/// Open an external http(s) URL in the OS default browser. The WebView blocks
/// window.open/target=_blank for external origins, so every outbound link in
/// the app routes through here (2026-07-11 beta feedback — links didn't open;
/// re-hit 2026-07-17: "Open posting" did nothing because this command was
/// missing from the rebuild's shell while the frontend already invoked it).
#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err(format!("refusing to open non-http(s) URL: {url}"));
    }
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("open").arg(&url).spawn();
    #[cfg(target_os = "windows")]
    let result = std::process::Command::new("cmd")
        .args(["/C", "start", "", &url])
        .spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let result = std::process::Command::new("xdg-open").arg(&url).spawn();
    result.map(|_| ()).map_err(|e| format!("could not open browser: {e}"))
}

/// Open the user's terminal running the named subscription CLI's login flow.
/// The terminal's own login shell resolves the binary on PATH (the same env
/// the sidecar's login-shell probe uses), and each CLI persists its auth
/// locally, so onboarding's Verify — which reads that persisted auth —
/// confirms success after they log in. Shown only when Verify reports
/// `not_logged_in`, so an already-logged-in user never lands here.
///
/// `cli` maps through a fixed allowlist to the exact command line — the
/// frontend can name a CLI, never inject a command. Unknown ids fall back to
/// `claude` (the historical behavior) rather than erroring: worst case the
/// user gets a terminal, not silence.
#[tauri::command]
fn open_login_terminal(cli: Option<String>) -> Result<(), String> {
    let login_cmd = match cli.as_deref() {
        Some("codex") => "codex login",
        Some("agy") => "agy", // first run triggers Antigravity's browser OAuth
        _ => "claude",
    };
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("osascript")
        .args([
            "-e",
            "tell application \"Terminal\" to activate",
            "-e",
            &format!("tell application \"Terminal\" to do script \"{login_cmd}\""),
        ])
        .spawn();
    #[cfg(target_os = "windows")]
    let result = std::process::Command::new("cmd")
        .args(["/C", "start", "cmd", "/K", login_cmd])
        .spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let result = std::process::Command::new("x-terminal-emulator")
        .args(["-e", login_cmd])
        .spawn();
    result.map(|_| ()).map_err(|e| format!("could not open terminal: {e}"))
}

/// Set the macOS dock / app-switcher icon at runtime to the finds-you-jobs logo.
///
/// A packaged `.app` gets its dock icon from `Contents/Resources/icon.icns` via
/// `CFBundleIconFile`, but `pnpm tauri dev` runs an unbundled debug binary that
/// has no such bundle, so it falls back to the default Tauri square. We embed
/// the logo bytes and hand them to `NSApplication` directly. Harmless in a
/// packaged build (it just re-asserts the same logo).
#[cfg(target_os = "macos")]
fn set_macos_dock_icon() {
    use objc2::{AnyThread, MainThreadMarker};
    use objc2_app_kit::{NSApplication, NSImage};
    use objc2_foundation::NSData;

    // Embedded at compile time — no runtime path lookup (icons/ isn't beside the
    // dev binary). Same source PNG the bundled icon.icns is generated from.
    const ICON_PNG: &[u8] = include_bytes!("../icons/icon.png");

    // Tauri's `setup` runs on the main thread; bail rather than panic if not.
    let Some(mtm) = MainThreadMarker::new() else {
        return;
    };
    let data = NSData::with_bytes(ICON_PNG);
    // SAFETY: `data` is a valid NSData; NSImage may return None for undecodable
    // bytes, which we handle. setApplicationIconImage with Some is well-defined.
    unsafe {
        if let Some(image) = NSImage::initWithData(NSImage::alloc(), &data) {
            NSApplication::sharedApplication(mtm).setApplicationIconImage(Some(&image));
        }
    }
}

/// Frontend reads the sidecar port through this command (architecture §4.4).
#[tauri::command]
fn get_sidecar_port(state: State<AppState>) -> Result<u16, String> {
    state
        .inner
        .lock()
        .unwrap()
        .info
        .as_ref()
        .map(|i| i.port)
        .ok_or_else(|| "sidecar not ready".to_string())
}

/// Frontend reads the bearer token through this command.
#[tauri::command]
fn get_api_token(state: State<AppState>) -> Result<String, String> {
    state
        .inner
        .lock()
        .unwrap()
        .info
        .as_ref()
        .map(|i| i.token.clone())
        .ok_or_else(|| "sidecar not ready".to_string())
}

/// Current supervision status (`ready` / `reconnecting` / `restarted` / …),
/// so the frontend can render an honest connection state.
#[tauri::command]
fn get_sidecar_status(state: State<AppState>) -> String {
    state.inner.lock().unwrap().status.clone()
}

/// AM1: a spawn/handshake failure must be user-visible, never a silent hang.
fn fatal_dialog(message: &str) {
    rfd::MessageDialog::new()
        .set_level(rfd::MessageLevel::Error)
        .set_title("finds-you-jobs — backend failed to start")
        .set_description(message)
        .set_buttons(rfd::MessageButtons::Ok)
        .show();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // Single-instance guard (2026-07-17 dogfood: two app windows, two
        // sidecars). A second launch focuses the existing window and exits.
        // Must be the FIRST plugin registered so it wins before any setup.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .manage(AppState::new())
        .invoke_handler(tauri::generate_handler![
            get_sidecar_port,
            get_api_token,
            get_sidecar_status,
            open_external,
            open_login_terminal,
        ])
        .setup(|app| {
            // Dev-mode dock icon: the unbundled `tauri dev` binary has no
            // .app bundle to source an icon from, so set it explicitly on macOS.
            #[cfg(target_os = "macos")]
            set_macos_dock_icon();

            let state: State<AppState> = app.state();
            let inner = state.inner.clone();
            let cwd = dev_cwd();

            match spawn_once(&cwd) {
                Ok((child, info)) => {
                    {
                        let mut guard = inner.lock().unwrap();
                        guard.child_pid = Some(child.id());
                        guard.info = Some(info);
                        guard.status = "ready".to_string();
                    }
                    let app_handle = app.handle().clone();
                    thread::spawn(move || supervise(app_handle, inner, child, cwd));
                }
                Err(err) => {
                    // AM1: fatal, visible, then exit — never a silent hang.
                    fatal_dialog(&format!(
                        "The finds-you-jobs backend did not start.\n\n{err}"
                    ));
                    app.handle().exit(1);
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the finds-you-jobs shell")
        .run(|app_handle, event| {
            // AM3: on quit, drain then force-kill the sidecar's process group.
            if let RunEvent::ExitRequested { .. } = event {
                let state: State<AppState> = app_handle.state();
                sidecar::shutdown(&state.inner);
            }
        });
}
