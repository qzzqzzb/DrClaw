use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::Manager;

struct DaemonState(Mutex<Option<Child>>);

/// Find the drclaw binary: check PATH first, then common venv locations.
fn find_drclaw() -> Result<PathBuf, String> {
    // Prefer project venv relative to the Tauri app (../../.venv/bin/drclaw),
    // so desktop uses the repo's current code instead of an older global install.
    let mut candidates: Vec<PathBuf> = vec![
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.venv/bin/drclaw"),
    ];
    if let Ok(home) = std::env::var("HOME") {
        candidates.push(PathBuf::from(home).join("Documents/GitHub/DrClaw/.venv/bin/drclaw"));
    }

    for candidate in candidates {
        if let Ok(canonical) = candidate.canonicalize() {
            if canonical.exists() {
                return Ok(canonical);
            }
        }
    }

    // Fallback to PATH only if repo-local virtualenv candidates are missing.
    if let Ok(output) = Command::new("which").arg("drclaw").output() {
        if output.status.success() {
            let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !path.is_empty() {
                return Ok(PathBuf::from(path));
            }
        }
    }

    Err("drclaw not found in project .venv or PATH. Run 'uv sync' in the repo root.".to_string())
}

#[tauri::command]
async fn start_daemon(state: tauri::State<'_, DaemonState>) -> Result<(), String> {
    let mut child_lock = state.0.lock().map_err(|e| e.to_string())?;
    if child_lock.is_some() {
        return Ok(()); // already running
    }

    let drclaw_path = find_drclaw()?;

    let child = Command::new(&drclaw_path)
        .args(["daemon", "-f", "web", "--debug-full"])
        .spawn()
        .map_err(|e| format!("Failed to spawn drclaw daemon: {e}"))?;

    *child_lock = Some(child);
    Ok(())
}

#[tauri::command]
async fn stop_daemon(state: tauri::State<'_, DaemonState>) -> Result<(), String> {
    let mut child_lock = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = child_lock.take() {
        child.kill().map_err(|e| format!("Failed to kill daemon: {e}"))?;
        let _ = child.wait(); // reap zombie
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(DaemonState(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![start_daemon, stop_daemon])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if let Some(state) = window.try_state::<DaemonState>() {
                    if let Ok(mut child_lock) = state.0.lock() {
                        if let Some(mut child) = child_lock.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
