//! finds-you-jobs Tauri shell — rarely-touched glue (architecture §4.1).
//! Spawns + supervises the Python sidecar, exposes the PORT/TOKEN handshake to
//! the frontend as Tauri commands, and kills the sidecar's process group on
//! quit. All the intelligence is in the sidecar; this is process management +
//! window lifecycle only.

mod sidecar;

use std::thread;

use tauri::{Manager, RunEvent, State};

use sidecar::{dev_cwd, spawn_once, supervise, AppState};

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
        .manage(AppState::new())
        .invoke_handler(tauri::generate_handler![
            get_sidecar_port,
            get_api_token,
            get_sidecar_status,
        ])
        .setup(|app| {
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
