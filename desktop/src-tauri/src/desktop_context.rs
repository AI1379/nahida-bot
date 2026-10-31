//! Small, content-free presence snapshot. Unknown native state stays unknown.

use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_store::StoreExt;

#[derive(Default)]
pub struct DesktopContext {
    quiet: AtomicBool,
}

pub fn snapshot(app: &AppHandle, sequence: u64) -> Value {
    let (locked, activity) = native_presence();
    let quiet = app.state::<DesktopContext>().quiet.load(Ordering::Relaxed);
    let visible = ["main", "pet"].iter().any(|label| {
        app.get_webview_window(label)
            .and_then(|window| window.is_focused().ok())
            .unwrap_or(false)
    });
    json!({"version": 1, "sequence": sequence, "locked": locked,
        "activity": activity, "do_not_disturb": quiet, "ui_visible": visible})
}

#[tauri::command]
pub fn desktop_context_read(app: AppHandle) -> Value {
    snapshot(&app, 0)
}

#[tauri::command]
pub fn desktop_context_set_quiet(app: AppHandle, state: State<'_, DesktopContext>, quiet: bool) -> Result<(), String> {
    let store = app.store("desktop-context.json").map_err(|error| error.to_string())?;
    store.set("quiet", json!(quiet));
    store.save().map_err(|error| error.to_string())?;
    state.quiet.store(quiet, Ordering::Relaxed);
    Ok(())
}

pub fn restore(app: &AppHandle) -> Result<(), String> {
    let store = app.store("desktop-context.json").map_err(|error| error.to_string())?;
    let quiet = store.get("quiet").and_then(|value| value.as_bool()).unwrap_or(false);
    app.state::<DesktopContext>().quiet.store(quiet, Ordering::Relaxed);
    Ok(())
}

#[cfg(not(windows))]
fn native_presence() -> (Option<bool>, &'static str) {
    (None, "unknown")
}

#[cfg(windows)]
fn native_presence() -> (Option<bool>, &'static str) {
    use windows_sys::Win32::System::StationsAndDesktops::{
        CloseDesktop, GetUserObjectInformationW, OpenInputDesktop, DESKTOP_READOBJECTS, UOI_NAME,
    };
    use windows_sys::Win32::System::SystemInformation::GetTickCount;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};

    // Failure to inspect the interactive desktop is not proof of an unlocked session.
    let locked = unsafe {
        let desktop = OpenInputDesktop(0, 0, DESKTOP_READOBJECTS);
        if desktop.is_null() {
            None
        } else {
            let mut name = [0u16; 128];
            let mut needed = 0u32;
            let ok = GetUserObjectInformationW(desktop, UOI_NAME, name.as_mut_ptr().cast(),
                std::mem::size_of_val(&name) as u32, &mut needed);
            CloseDesktop(desktop);
            if ok == 0 {
                None
            } else {
                let end = name.iter().position(|value| *value == 0).unwrap_or(name.len());
                Some(String::from_utf16_lossy(&name[..end]) != "Default")
            }
        }
    };
    let mut info = LASTINPUTINFO { cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32, dwTime: 0 };
    let activity = unsafe {
        if GetLastInputInfo(&mut info) == 0 {
            "unknown"
        } else if GetTickCount().wrapping_sub(info.dwTime) < 120_000 {
            "active"
        } else {
            "idle"
        }
    };
    (locked, activity)
}
