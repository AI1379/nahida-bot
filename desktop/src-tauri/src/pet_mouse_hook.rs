//! Double-click detection for the click-through pet window.
//!
//! The pet window ignores cursor events in every state except chat, so the
//! WebView never sees clicks on the model. A low-level mouse hook watches
//! global left-button presses, recognizes a double-click inside the pet
//! window rectangle while that window is click-through, and brings the main
//! window up. The hook only posts a message (low-level hooks must return
//! fast); the subclass procedure does the actual window management. In chat
//! mode the window stops being click-through, the hook stands down, and the
//! WebView reports the double-click through the command bridge instead.

use std::sync::atomic::{AtomicI32, AtomicI64, AtomicUsize, Ordering};
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use tauri::Manager;

use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::GetDoubleClickTime;
use windows_sys::Win32::UI::Shell::{DefSubclassProc, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, GetSystemMetrics, GetWindowLongPtrW, GetWindowRect, MSLLHOOKSTRUCT,
    PostMessageW, SetWindowsHookExW, UnhookWindowsHookEx, GWL_EXSTYLE, HHOOK, SM_CXDOUBLECLK,
    SM_CYDOUBLECLK, WH_MOUSE_LL, WM_LBUTTONDOWN, WS_EX_TRANSPARENT,
};

const WM_OPEN_MAIN_FROM_PET: u32 = 0x8001; // WM_APP + 1
const SUBCLASS_ID: usize = 0x4e41_4850; // "NAHP"

static PET_HWND: AtomicUsize = AtomicUsize::new(0);
static APP_HANDLE: OnceLock<tauri::AppHandle> = OnceLock::new();
static HOOK: AtomicUsize = AtomicUsize::new(0);
static LAST_CLICK_MS: AtomicI64 = AtomicI64::new(0);
static LAST_CLICK_X: AtomicI32 = AtomicI32::new(i32::MIN);
static LAST_CLICK_Y: AtomicI32 = AtomicI32::new(i32::MIN);

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as i64)
        .unwrap_or(0)
}

unsafe extern "system" fn subclass_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    _data: usize,
) -> LRESULT {
    if msg == WM_OPEN_MAIN_FROM_PET {
        if let Some(app) = APP_HANDLE.get() {
            crate::show_main_window(app);
        }
        return 0;
    }
    unsafe { DefSubclassProc(hwnd, msg, wparam, lparam) }
}

unsafe extern "system" fn mouse_hook_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code >= 0 && wparam as u32 == WM_LBUTTONDOWN {
        unsafe {
            let info = &*(lparam as *const MSLLHOOKSTRUCT);
            let now = now_ms();
            let last = LAST_CLICK_MS.load(Ordering::Relaxed);
            let dx = info
                .pt
                .x
                .saturating_sub(LAST_CLICK_X.load(Ordering::Relaxed))
                .abs();
            let dy = info
                .pt
                .y
                .saturating_sub(LAST_CLICK_Y.load(Ordering::Relaxed))
                .abs();
            LAST_CLICK_MS.store(now, Ordering::Relaxed);
            LAST_CLICK_X.store(info.pt.x, Ordering::Relaxed);
            LAST_CLICK_Y.store(info.pt.y, Ordering::Relaxed);

            // Raw low-level clicks never synthesize WM_LBUTTONDBLCLK, so
            // match the OS definition: two presses within the double-click
            // time and inside the double-click rectangle.
            let double_click = now.saturating_sub(last) <= GetDoubleClickTime() as i64
                && dx <= GetSystemMetrics(SM_CXDOUBLECLK)
                && dy <= GetSystemMetrics(SM_CYDOUBLECLK);

            if double_click {
                let pet = PET_HWND.load(Ordering::Relaxed) as HWND;
                if !pet.is_null() {
                    let ex_style = GetWindowLongPtrW(pet, GWL_EXSTYLE) as u32;
                    // Interactive (chat) windows receive the double-click in
                    // the WebView; the hook must not trigger a second time.
                    if ex_style & WS_EX_TRANSPARENT != 0 {
                        let mut rect = RECT {
                            left: 0,
                            top: 0,
                            right: 0,
                            bottom: 0,
                        };
                        if GetWindowRect(pet, &mut rect) != 0
                            && info.pt.x >= rect.left
                            && info.pt.x < rect.right
                            && info.pt.y >= rect.top
                            && info.pt.y < rect.bottom
                        {
                            PostMessageW(pet, WM_OPEN_MAIN_FROM_PET, 0, 0);
                        }
                    }
                }
            }
        }
    }
    unsafe {
        CallNextHookEx(
            HOOK.load(Ordering::Relaxed) as HHOOK,
            code,
            wparam,
            lparam,
        )
    }
}

/// Install the hook and the subclass. Must run on the thread that pumps
/// messages (Tauri's setup runs there).
pub fn install(app: &tauri::App) {
    let Some(pet) = app.get_webview_window("pet") else {
        return;
    };
    let Ok(pet_hwnd) = pet.hwnd() else {
        return;
    };

    let _ = APP_HANDLE.set(app.handle().clone());
    PET_HWND.store(pet_hwnd.0 as usize, Ordering::Relaxed);

    unsafe {
        SetWindowSubclass(pet_hwnd.0 as HWND, Some(subclass_proc), SUBCLASS_ID, 0);
        let module = GetModuleHandleW(std::ptr::null());
        let hook = SetWindowsHookExW(WH_MOUSE_LL, Some(mouse_hook_proc), module, 0);
        if !hook.is_null() {
            HOOK.store(hook as usize, Ordering::Relaxed);
        }
    }
}

pub fn uninstall() {
    let hook = HOOK.swap(0, Ordering::Relaxed) as HHOOK;
    if !hook.is_null() {
        unsafe { UnhookWindowsHookEx(hook) };
    }
}
