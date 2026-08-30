mod computer_use;
mod gateway_node;
mod motion_dataset;
#[cfg(windows)]
mod pet_mouse_hook;
mod remote_control;
mod secure_storage;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Emitter, Manager, RunEvent, WindowEvent};

#[tauri::command]
fn runtime_mode() -> &'static str {
    "mock"
}

/// Remove the latent caption styles from the pet window.
///
/// tao keeps `WS_CAPTION | WS_SYSMENU` on every window even with
/// `decorations: false` (it hides the frame via WM_NCCALCSIZE instead of
/// dropping the styles), and it re-applies them whenever a window flag
/// changes (e.g. `setIgnoreCursorEvents`). When the pet window gets
/// activated for chat input, `DefWindowProc(WM_NCACTIVATE)` then paints a
/// legacy classic title bar from those styles. Stripping the styles leaves
/// the legacy path with nothing to draw.
#[cfg(windows)]
fn strip_window_chrome(window: &tauri::WebviewWindow) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowLongPtrW, SetWindowPos, GWL_STYLE, SWP_FRAMECHANGED,
        SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, WS_CAPTION, WS_SYSMENU,
    };

    let Ok(hwnd) = window.hwnd() else {
        return;
    };
    let hwnd = hwnd.0 as windows_sys::Win32::Foundation::HWND;

    unsafe {
        let style = GetWindowLongPtrW(hwnd, GWL_STYLE) as u32;
        let cleaned = style & !(WS_CAPTION | WS_SYSMENU);
        if cleaned != style {
            SetWindowLongPtrW(hwnd, GWL_STYLE, cleaned as isize);
            SetWindowPos(
                hwnd,
                std::ptr::null_mut(),
                0,
                0,
                0,
                0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
            );
        }
    }
}

#[cfg(not(windows))]
fn strip_window_chrome(_window: &tauri::WebviewWindow) {}

/// Swallow every legacy non-client paint message on the pet window.
///
/// Stripping the styles is not enough: activation (`WM_NCACTIVATE`) can
/// paint a classic caption strip before the styles are re-stripped, and
/// those pixels stick. Subclassing the window lets us forward
/// `WM_NCACTIVATE` with `lParam = -1` (documented to suppress the repaint)
/// and drop `WM_NCPAINT` plus the undocumented themed-caption messages
/// (0x00AE/0x00AF) entirely, so the legacy frame is never drawn.
#[cfg(windows)]
fn suppress_nc_paint(window: &tauri::WebviewWindow) {
    use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
    use windows_sys::Win32::UI::Shell::{DefSubclassProc, SetWindowSubclass};
    use windows_sys::Win32::UI::WindowsAndMessaging::{WM_NCACTIVATE, WM_NCPAINT};

    const WM_NCUAHDRAWCAPTION: u32 = 0x00AE;
    const WM_NCUAHDRAWFRAME: u32 = 0x00AF;
    const SUBCLASS_ID: usize = 0x4e41_4849; // "NAHI"

    unsafe extern "system" fn pet_subclass_proc(
        hwnd: HWND,
        msg: u32,
        wparam: WPARAM,
        lparam: LPARAM,
        _id: usize,
        _data: usize,
    ) -> LRESULT {
        match msg {
            WM_NCPAINT | WM_NCUAHDRAWCAPTION | WM_NCUAHDRAWFRAME => 0,
            WM_NCACTIVATE => unsafe { DefSubclassProc(hwnd, msg, wparam, -1) },
            _ => unsafe { DefSubclassProc(hwnd, msg, wparam, lparam) },
        }
    }

    let Ok(hwnd) = window.hwnd() else {
        return;
    };
    let hwnd = hwnd.0 as HWND;

    unsafe {
        SetWindowSubclass(hwnd, Some(pet_subclass_proc), SUBCLASS_ID, 0);
    }
}

#[cfg(not(windows))]
fn suppress_nc_paint(_window: &tauri::WebviewWindow) {}

/// Bring the main window back from the tray. Must go through the Tauri
/// APIs (not raw ShowWindow) so tao's visibility bookkeeping stays in
/// sync; a raw show would leave tao believing the window is hidden and
/// a later `hide()` would be diffed away into a no-op.
pub(crate) fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn emit_pet_command(app: &tauri::AppHandle, command_type: &str) {
    let _ = app.emit_to(
        "main",
        "nahida://desktop/pet-command",
        serde_json::json!({ "type": command_type }),
    );
}

/// Invoked by the frontend right after toggling click-through, because tao
/// re-applies the caption styles on that change.
#[tauri::command]
fn polish_pet_window(window: tauri::WebviewWindow) {
    if window.label() == "pet" {
        strip_window_chrome(&window);
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // tokio-tungstenite pulls rustls without a default crypto provider;
    // select ring up front so the first TLS connect doesn't panic on
    // provider resolution.
    let _ = rustls::crypto::ring::default_provider().install_default();

    tauri::Builder::default()
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .manage(gateway_node::GatewayNodeManager::default())
        .invoke_handler(tauri::generate_handler![
            runtime_mode,
            polish_pet_window,
            gateway_node::gateway_node_connect,
            gateway_node::gateway_node_disconnect,
            gateway_node::gateway_node_status,
            gateway_node::gateway_node_submit_input,
            gateway_node::gateway_node_complete_capability,
            motion_dataset::motion_dataset_append,
            motion_dataset::motion_dataset_read,
            motion_dataset::motion_dataset_export,
            motion_dataset::motion_dataset_clear,
            remote_control::remote_control_policy_read,
            remote_control::remote_control_policy_save,
            secure_storage::secure_tokens_read,
            secure_storage::secure_tokens_write,
        ])
        .setup(|app| {
            let remote_control = remote_control::RemoteControlManager::load(app.handle())
                .map_err(std::io::Error::other)?;
            app.manage(remote_control);
            let motion_dataset = motion_dataset::MotionDatasetManager::load(app.handle())
                .map_err(std::io::Error::other)?;
            app.manage(motion_dataset);
            if let Some(pet) = app.get_webview_window("pet") {
                suppress_nc_paint(&pet);
                strip_window_chrome(&pet);
            }
            #[cfg(windows)]
            pet_mouse_hook::install(app);
            let show_main = MenuItem::with_id(app, "show-main", "打开主窗口", true, None::<&str>)?;
            let show_pet = MenuItem::with_id(app, "show-pet", "唤出桌宠", true, None::<&str>)?;
            let chat = MenuItem::with_id(app, "chat", "打开对话", true, None::<&str>)?;
            let hide_pet = MenuItem::with_id(app, "hide-pet", "收起桌宠", true, None::<&str>)?;
            let pomodoro =
                MenuItem::with_id(app, "pomodoro", "开始 / 暂停专注", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[&show_main, &show_pet, &chat, &hide_pet, &pomodoro, &quit],
            )?;
            let mut tray = TrayIconBuilder::with_id("nahida-tray")
                .tooltip("Nahida Desktop")
                .menu(&menu)
                // Right click opens the menu; left click reopens the main window.
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show-main" => show_main_window(app),
                    "show-pet" => emit_pet_command(app, "emerge"),
                    "chat" => emit_pet_command(app, "enter_chat"),
                    "hide-pet" => emit_pet_command(app, "retreat"),
                    "pomodoro" => emit_pet_command(app, "toggle_pomodoro"),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Activation repaints the non-client area; keep the pet window
            // chrome-free even if a style refresh slipped past the command.
            if window.label() == "pet" && matches!(event, WindowEvent::Focused(_)) {
                if let Some(pet) = window.get_webview_window("pet") {
                    strip_window_chrome(&pet);
                }
            }
            // Closing hides to the tray so the gateway node and pet runtime
            // keep running; quitting goes through the tray menu instead.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to run Nahida Desktop")
        .run(|_app, event| {
            // The last window going away requests an uncoded exit; stay
            // resident in the tray. The tray quit item calls app.exit(0),
            // which carries a code and is allowed through.
            match event {
                RunEvent::ExitRequested {
                    code: None, api, ..
                } => api.prevent_exit(),
                RunEvent::Exit => {
                    #[cfg(windows)]
                    pet_mouse_hook::uninstall();
                }
                _ => {}
            }
        });
}
