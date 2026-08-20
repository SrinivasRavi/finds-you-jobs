//! finds-you-jobs Tauri shell — rarely-touched glue (architecture section 4.1).
//! Spawns + supervises the Python sidecar, exposes the PORT/TOKEN handshake to
//! the frontend as Tauri commands, and kills the sidecar's process group on
//! quit. All the intelligence is in the sidecar; this is process management +
//! window lifecycle only.

mod sidecar;

use std::thread;

use tauri::{Manager, RunEvent, State};

use sidecar::{dev_cwd, init_shell_log, spawn_once, supervise, AppState};

/// Open an external http(s) URL in the OS default browser. The WebView blocks
/// window.open/target=_blank for external origins, so every outbound link in
/// the app routes through here (2026-07-11 beta feedback — links didn't open;
/// re-hit 2026-07-17: "Open posting" did nothing because this command was
/// missing from the rebuild's shell while the frontend already invoked it).
///
/// URLs reaching here come from scraped postings and LLM output — untrusted.
/// `validate_external_url` strictly parses them, and every platform arm spawns
/// the launcher with a real argv, never through a shell. Windows in particular
/// must not use `cmd /C start`: cmd.exe re-tokenizes its line, so `& | ^` in a
/// URL become command separators (F-H1 injection). `rundll32
/// url.dll,FileProtocolHandler` opens the default browser with plain argv
/// semantics instead.
#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    let url = validate_external_url(&url)?;
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("open").arg(&url).spawn();
    #[cfg(target_os = "windows")]
    let result = std::process::Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", &url])
        .spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let result = std::process::Command::new("xdg-open").arg(&url).spawn();
    result.map(|_| ()).map_err(|e| format!("could not open browser: {e}"))
}

/// Strict validation for untrusted outbound URLs (F-H1): absolute http(s) with
/// a real host, no embedded credentials, no whitespace or control characters.
/// Returns the parser's normalized serialization — shell-sensitive bytes like
/// `"` and spaces come back percent-encoded — and THAT string, not the raw
/// input, is what gets spawned.
fn validate_external_url(raw: &str) -> Result<String, String> {
    // The WHATWG parser silently strips tab/newline and trims C0/space; a URL
    // carrying those was never a clean link — reject instead of laundering.
    if raw.chars().any(|c| c.is_ascii_control() || c == ' ') {
        return Err("refusing to open URL with whitespace or control characters".to_string());
    }
    let parsed =
        url::Url::parse(raw).map_err(|e| format!("refusing to open invalid URL: {e}"))?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err(format!("refusing to open non-http(s) URL: {raw}"));
    }
    if parsed.host_str().is_none() {
        return Err(format!("refusing to open URL without a host: {raw}"));
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(format!("refusing to open URL with embedded credentials: {raw}"));
    }
    Ok(parsed.to_string())
}

/// Open the user's terminal running the named subscription CLI's login flow.
/// The terminal's own login shell resolves the binary on PATH (the same env
/// the sidecar's login-shell probe uses), and each CLI persists its auth
/// locally, so onboarding's Verify — which reads that persisted auth —
/// confirms success after they log in. Shown only when Verify reports
/// `not_logged_in`, so an already-logged-in user never lands here.
///
/// `cli` maps through a fixed allowlist to the exact command line — the
/// frontend can name a CLI, never inject a command. An unknown id is an error
/// (F-L6): silently falling back to `claude` would open the wrong login flow
/// and hide the frontend/shell mismatch. `None` still means `claude` (the
/// historical default).
#[tauri::command]
fn open_login_terminal(cli: Option<String>) -> Result<(), String> {
    let login_cmd = match cli.as_deref() {
        None | Some("claude") => "claude",
        Some("codex") => "codex login",
        Some("agy") => "agy", // first run triggers Antigravity's browser OAuth
        Some(other) => return Err(format!("unknown login CLI: {other}")),
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

/// Frontend reads the sidecar port through this command (architecture section 4.4).
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
            // In-app software update (desktop only): the updater checks the
            // pinned endpoint, verifies the Ed25519 signature, and installs;
            // the process plugin relaunches afterward. Registered here (not in
            // the builder chain) so it's cleanly gated off any mobile target.
            #[cfg(desktop)]
            {
                app.handle()
                    .plugin(tauri_plugin_updater::Builder::new().build())?;
                app.handle().plugin(tauri_plugin_process::init())?;
            }

            // Dev-mode dock icon: the unbundled `tauri dev` binary has no
            // .app bundle to source an icon from, so set it explicitly on macOS.
            #[cfg(target_os = "macos")]
            set_macos_dock_icon();

            // Pin the shell.log directory before the first log line — packaged
            // installs use the OS app-log dir, dev keeps repo-local logs/ (F-L5).
            init_shell_log(app.handle());

            let state: State<AppState> = app.state();
            let inner = state.inner.clone();
            let cwd = dev_cwd();

            match spawn_once(&cwd, app.handle()) {
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

#[cfg(test)]
mod tests {
    use super::validate_external_url;

    #[test]
    fn accepts_clean_http_and_https() {
        assert_eq!(
            validate_external_url("https://jobs.example.com/posting/123?src=feed&x=1").unwrap(),
            "https://jobs.example.com/posting/123?src=feed&x=1"
        );
        assert!(validate_external_url("http://example.com/").is_ok());
        // Scheme case is normalized, not rejected (frontend's check is
        // case-insensitive too).
        assert_eq!(
            validate_external_url("HTTPS://Example.COM/a").unwrap(),
            "https://example.com/a"
        );
    }

    #[test]
    fn rejects_non_http_schemes() {
        assert!(validate_external_url("file:///etc/passwd").is_err());
        assert!(validate_external_url("javascript:alert(1)").is_err());
        assert!(validate_external_url("ftp://example.com/").is_err());
        assert!(validate_external_url("not a url").is_err());
        assert!(validate_external_url("").is_err());
        // Relative / schemeless input must not slip through.
        assert!(validate_external_url("//example.com/x").is_err());
        assert!(validate_external_url("example.com").is_err());
    }

    #[test]
    fn rejects_whitespace_and_control_characters() {
        // The WHATWG parser would silently strip these — we reject instead.
        assert!(validate_external_url("https://x.example/a\nb").is_err());
        assert!(validate_external_url("https://x.example/a\tb").is_err());
        assert!(validate_external_url("https://x.example/a b").is_err());
        assert!(validate_external_url(" https://x.example/").is_err());
        assert!(validate_external_url("https://x.example/\r").is_err());
        assert!(validate_external_url("https://x.example/\u{0}").is_err());
    }

    #[test]
    fn rejects_missing_host_and_embedded_credentials() {
        assert!(validate_external_url("https://").is_err());
        assert!(validate_external_url("https://user:pass@x.example/").is_err());
        assert!(validate_external_url("https://user@x.example/").is_err());
        // WHATWG leniency: a slash-count typo normalizes into a host-bearing
        // URL instead of failing — safe, because the normalized serialization
        // (not the raw input) is what gets spawned.
        assert_eq!(
            validate_external_url("https:///path-only").unwrap(),
            "https://path-only/"
        );
    }

    #[test]
    fn windows_metacharacters_are_inert_argv_data() {
        // The F-H1 PoC: under `cmd /C start` this ran calc.exe. The validated
        // string is passed as one argv element to rundll32/open/xdg-open, so
        // `&` is plain URL data — but it must survive validation, since & is
        // legitimate in query strings.
        let ok = validate_external_url("https://x.example/&&calc.exe").unwrap();
        assert_eq!(ok, "https://x.example/&&calc.exe");
        // Raw double quotes never survive serialization (percent-encoded), so
        // the spawned argument can't confuse any downstream quoting.
        let quoted = validate_external_url("https://x.example/a\"b?c=\"d").unwrap();
        assert!(!quoted.contains('"'), "serialized URL still has a raw quote: {quoted}");
        let caret = validate_external_url("https://x.example/a^b|c").unwrap();
        assert!(caret.starts_with("https://x.example/"));
    }
}
