//! Sidecar lifecycle — the §4.4 contract (AM1–AM3), decided numbers built in.
//!
//! Spawn the Python sidecar, capture its `PORT=`/`TOKEN=` handshake (20 s cap),
//! supervise `/healthz` with a restart cap (3 failures in 30 s, exponential
//! backoff, ≥ 60 s-healthy reset), and on quit drain (10 s) then force-kill the
//! whole process group so no grandchildren (claude CLI / Chromium / voyager)
//! survive.

use std::io::{BufRead, BufReader, Error, ErrorKind};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter};

// --- Decided lifecycle numbers (architecture §4.4 / ROADMAP §A0.5) ---
pub const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(20); // AM1
const HEALTH_POLL_INTERVAL: Duration = Duration::from_secs(2);
const RESTART_WINDOW: Duration = Duration::from_secs(30); // AM2
const MAX_FAILURES: u32 = 3; // AM2
const HEALTHY_RESET: Duration = Duration::from_secs(60); // AM2
const SHUTDOWN_DRAIN: Duration = Duration::from_secs(10); // AM3
// Startup grace: the sidecar prints its handshake BEFORE the Python lifespan
// runs (migrations on first boot), and uvicorn only LISTENS after the lifespan
// finishes — on a cold Windows laptop with antivirus scanning every .py file
// that is tens of seconds. "Not listening yet" right after a (re)spawn is a
// boot phase, not a crash: counting it killed healthy booting sidecars in a
// 2.5 s loop on three real Windows installs (2026-07-19). A dead PROCESS is
// still detected immediately via try_wait inside the grace.
const STARTUP_GRACE: Duration = Duration::from_secs(180);

/// PROD sidecar binary (PyInstaller onedir) relative to the app resource dir.
/// Wired at packaging time (A0.6 / Track A5); placeholder constant for now —
/// dev uses `uv run python -m sidecar.app` instead.
const PROD_SIDECAR_REL: &str = "sidecar/fyj-sidecar";

#[derive(Clone, Debug, Serialize)]
pub struct SidecarInfo {
    pub port: u16,
    pub token: String,
}

/// Shared, Tauri-managed sidecar state. The commands read `info`; the exit
/// handler flips `stopping` and reads `child_pid`.
#[derive(Default)]
pub struct Inner {
    pub info: Option<SidecarInfo>,
    pub child_pid: Option<u32>,
    pub status: String,
    pub stopping: bool,
}

#[derive(Clone)]
pub struct AppState {
    pub inner: Arc<Mutex<Inner>>,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(Inner::default())),
        }
    }
}

impl Default for AppState {
    fn default() -> Self {
        Self::new()
    }
}

/// Repo root in dev = the parent of `src-tauri/` (this crate's manifest dir).
pub fn dev_cwd() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn build_command(cwd: &Path) -> Command {
    if cfg!(debug_assertions) {
        let mut cmd = Command::new("uv");
        cmd.args(["run", "python", "-m", "sidecar.app"])
            .current_dir(cwd);
        cmd
    } else {
        // PROD path finalized at packaging (A0.6). Constant kept clearly marked.
        Command::new(PROD_SIDECAR_REL)
    }
}

/// Spawn the sidecar and block until the handshake arrives or 20 s elapses.
pub fn spawn_once(cwd: &Path) -> std::io::Result<(Child, SidecarInfo)> {
    let mut cmd = build_command(cwd);
    cmd.stdout(Stdio::piped()).stderr(Stdio::inherit());
    // The sidecar's orphan watchdog watches THIS pid (not just its immediate
    // parent, which in dev is the `uv run` wrapper that outlives us) — so a
    // hard-killed shell always takes the sidecar down within one poll tick.
    cmd.env("FYJ_SHELL_PID", std::process::id().to_string());
    // Put the child in its own process group so we can kill the whole tree.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }

    let mut child = cmd.spawn()?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| Error::new(ErrorKind::Other, "sidecar stdout not piped"))?;

    let (tx, rx) = mpsc::channel::<SidecarInfo>();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        let mut port: Option<u16> = None;
        let mut token: Option<String> = None;
        let mut sent = false;
        // Keep draining after the handshake so a full stdout pipe never stalls
        // the sidecar.
        for line in reader.lines() {
            let Ok(line) = line else { break };
            if sent {
                continue;
            }
            if let Some(rest) = line.strip_prefix("PORT=") {
                port = rest.trim().parse::<u16>().ok();
            } else if let Some(rest) = line.strip_prefix("TOKEN=") {
                token = Some(rest.trim().to_string());
            }
            if let (Some(p), Some(t)) = (port, token.clone()) {
                let _ = tx.send(SidecarInfo { port: p, token: t });
                sent = true;
            }
        }
    });

    match rx.recv_timeout(HANDSHAKE_TIMEOUT) {
        Ok(info) => Ok((child, info)),
        Err(_) => {
            kill_group(child.id());
            let _ = child.wait();
            Err(Error::new(
                ErrorKind::TimedOut,
                "sidecar handshake timed out (20s)",
            ))
        }
    }
}

/// GET /healthz — the open liveness probe.
pub fn health_ok(port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/healthz");
    match ureq::get(&url).timeout(Duration::from_secs(2)).call() {
        Ok(resp) => resp.status() == 200,
        Err(_) => false,
    }
}

/// POST /shutdown — asks the sidecar to drain + exit on its own.
pub fn post_shutdown(port: u16, token: &str) {
    let url = format!("http://127.0.0.1:{port}/shutdown");
    let _ = ureq::post(&url)
        .set("Authorization", &format!("Bearer {token}"))
        .timeout(Duration::from_secs(5))
        .call();
}

/// Force-kill the sidecar's whole process group (no surviving grandchildren).
pub fn kill_group(pid: u32) {
    #[cfg(unix)]
    unsafe {
        // Negative pid targets the process group (child is its group leader).
        libc::kill(-(pid as i32), libc::SIGKILL);
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .status();
    }
}

/// Append one timestamped line to `logs/shell.log` (dev diagnostics — the
/// remote-debugging channel for real installs: the console can't say who
/// initiated an exit, this file can). Best-effort, never fails the caller.
pub fn shell_log(msg: &str) {
    use std::io::Write;
    let dir = dev_cwd().join("logs");
    let _ = std::fs::create_dir_all(&dir);
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("shell.log"))
    {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let _ = writeln!(f, "[{ts}] {msg}");
    }
}

fn emit_status(app: &AppHandle, state: &Arc<Mutex<Inner>>, status: &str, port: u16) {
    {
        let mut s = state.lock().unwrap();
        s.status = status.to_string();
    }
    let _ = app.emit(
        "sidecar://status",
        serde_json::json!({ "status": status, "port": port }),
    );
}

fn emit_fatal(app: &AppHandle, message: &str) {
    let _ = app.emit(
        "sidecar://fatal",
        serde_json::json!({ "message": message }),
    );
}

/// Block until the sidecar answers /healthz once after a (re)spawn, or the
/// startup grace runs out, or the PROCESS exits (a real crash — detected
/// immediately, no grace). Returns true when healthy or the app is stopping.
fn wait_until_healthy(child: &mut Child, port: u16, state: &Arc<Mutex<Inner>>) -> bool {
    let deadline = Instant::now() + STARTUP_GRACE;
    while Instant::now() < deadline {
        if state.lock().unwrap().stopping {
            return true; // the caller's loop observes `stopping` and exits
        }
        if let Ok(Some(_)) = child.try_wait() {
            return false; // process died during boot — genuine failure
        }
        if health_ok(port) {
            return true;
        }
        thread::sleep(Duration::from_millis(500));
    }
    false
}

/// The supervision loop (runs on its own thread). Owns the child handle.
pub fn supervise(app: AppHandle, state: Arc<Mutex<Inner>>, mut child: Child, cwd: PathBuf) {
    let mut failures: u32 = 0;
    let mut first_failure_at: Option<Instant> = None;
    let mut healthy_since: Option<Instant> = None;

    // Startup grace for the initial spawn: the health loop below treats an
    // unanswered /healthz as a crash signal, which is only fair once the
    // sidecar has finished booting (migrations) and answered once.
    {
        let port = state.lock().unwrap().info.as_ref().map(|i| i.port);
        if let Some(port) = port {
            if wait_until_healthy(&mut child, port, &state) {
                // Loud + human: the dev console otherwise goes silent at the
                // exact moment boot SUCCEEDS, which reads as a hang (three
                // real installs Ctrl-C'd healthy boots, 2026-07-18/19). Say
                // explicitly what this terminal is for — Ctrl-C here QUITS
                // the app, which is not obvious.
                eprintln!(
                    "finds-you-jobs: backend ready on 127.0.0.1:{port} — use the app window.\n\
                     finds-you-jobs: keep this terminal open while the app runs; press Ctrl-C HERE only when you want to quit the app."
                );
                shell_log(&format!("ready: backend healthy on port {port}"));
                emit_status(&app, &state, "ready", port);
            } else {
                shell_log("startup grace expired or sidecar exited before first healthy answer");
            }
            // On false: fall through — the loop's checks now fail fast and
            // drive the existing restart/fatal machinery.
        }
    }

    loop {
        thread::sleep(HEALTH_POLL_INTERVAL);

        if state.lock().unwrap().stopping {
            let _ = child.wait();
            return;
        }

        let port = match state.lock().unwrap().info.as_ref() {
            Some(info) => info.port,
            None => continue,
        };

        if health_ok(port) {
            match healthy_since {
                None => healthy_since = Some(Instant::now()),
                Some(since) if since.elapsed() >= HEALTHY_RESET => {
                    failures = 0;
                    first_failure_at = None;
                }
                _ => {}
            }
            emit_status(&app, &state, "ready", port);
            continue;
        }

        // Unhealthy.
        healthy_since = None;
        match first_failure_at {
            Some(started) if started.elapsed() <= RESTART_WINDOW => {}
            _ => {
                first_failure_at = Some(Instant::now());
                failures = 0;
            }
        }
        failures += 1;
        shell_log(&format!("unhealthy: /healthz failed (failure {failures}/{MAX_FAILURES})"));
        emit_status(&app, &state, "reconnecting", port);

        if failures >= MAX_FAILURES {
            shell_log("fatal: restart cap hit — giving up and killing the sidecar");
            emit_fatal(
                &app,
                "backend crashed repeatedly (3 failures within 30s) — giving up",
            );
            kill_group(child.id());
            let _ = child.wait();
            return;
        }

        // Exponential backoff: 0.5s, 1s, 2s…
        let backoff = Duration::from_millis(500 * 2u64.pow(failures - 1));
        thread::sleep(backoff);

        if state.lock().unwrap().stopping {
            let _ = child.wait();
            return;
        }

        // Restart: kill the old group, respawn, publish the new handshake.
        kill_group(child.id());
        let _ = child.wait();
        match spawn_once(&cwd) {
            Ok((new_child, info)) => {
                {
                    let mut s = state.lock().unwrap();
                    s.child_pid = Some(new_child.id());
                    s.info = Some(info.clone());
                }
                child = new_child;
                shell_log(&format!("restarted: new sidecar on port {}", info.port));
                // The respawned sidecar gets the same boot grace before the
                // failure counting resumes — a restart re-runs the lifespan.
                if wait_until_healthy(&mut child, info.port, &state) {
                    emit_status(&app, &state, "restarted", info.port);
                } else {
                    emit_status(&app, &state, "reconnecting", info.port);
                }
            }
            Err(err) => {
                emit_fatal(&app, &format!("backend restart failed: {err}"));
                return;
            }
        }
    }
}

/// Graceful shutdown on quit: drain (POST /shutdown, wait ≤ 10 s), then force-kill.
pub fn shutdown(state: &Arc<Mutex<Inner>>) {
    // The one place every intentional exit funnels through — window closed,
    // console Ctrl-C, quit menu. Its presence in shell.log separates "user
    // quit" from "something died" when reading a report from a real install.
    shell_log("quit requested (window closed / console Ctrl-C / quit) — draining sidecar");
    let (pid, info) = {
        let mut s = state.lock().unwrap();
        s.stopping = true;
        (s.child_pid, s.info.clone())
    };

    if let Some(info) = &info {
        post_shutdown(info.port, &info.token);
        let deadline = Instant::now() + SHUTDOWN_DRAIN;
        while Instant::now() < deadline {
            if !health_ok(info.port) {
                break;
            }
            thread::sleep(Duration::from_millis(200));
        }
    }

    if let Some(pid) = pid {
        kill_group(pid);
    }
}
