use base64::Engine as _;
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager};

use crate::remote_control::{
    authorize, RemoteControlError, RemoteControlManager, RemoteControlPolicy,
};

pub const SCREENSHOT_CAPABILITY: &str = "desktop.computer.screenshot";
pub const INPUT_CAPABILITY: &str = "desktop.computer.input";

const NORMALIZED_COORDINATE_MAX: i32 = 1_000;
const MAX_CAPTURE_DIMENSION: u32 = 1_600;
const MAX_TYPED_UTF16_UNITS: usize = 2_000;
const MAX_HOTKEY_KEYS: usize = 8;
const MAX_SCROLL_STEPS: i32 = 10;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ScreenshotArguments {
    actor_account_key: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct InputArguments {
    actor_account_key: String,
    action: String,
    #[serde(default)]
    x: Option<i32>,
    #[serde(default)]
    y: Option<i32>,
    #[serde(default = "default_mouse_button")]
    button: String,
    #[serde(default = "default_clicks")]
    clicks: u8,
    #[serde(default)]
    scroll_steps: i32,
    #[serde(default)]
    text: String,
    #[serde(default)]
    keys: Vec<String>,
}

fn default_mouse_button() -> String {
    "left".to_string()
}

fn default_clicks() -> u8 {
    1
}

pub async fn execute(
    app: &AppHandle,
    capability: &str,
    arguments: Map<String, Value>,
) -> Result<Value, RemoteControlError> {
    let policy = app.state::<RemoteControlManager>().snapshot();
    match capability {
        SCREENSHOT_CAPABILITY => {
            let arguments = parse_screenshot_arguments(arguments)?;
            authorize_computer_use(&policy, &arguments.actor_account_key, false)?;
            let frame = tauri::async_runtime::spawn_blocking(capture_desktop)
                .await
                .map_err(|error| {
                    RemoteControlError::new(
                        "screen_capture_failed",
                        format!("screen capture worker failed: {error}"),
                    )
                })??;
            Ok(frame)
        }
        INPUT_CAPABILITY => {
            let arguments = parse_input_arguments(arguments)?;
            authorize_computer_use(&policy, &arguments.actor_account_key, true)?;
            tauri::async_runtime::spawn_blocking(move || apply_input(arguments))
                .await
                .map_err(|error| {
                    RemoteControlError::new("input_failed", format!("input worker failed: {error}"))
                })?
        }
        _ => Err(RemoteControlError::new(
            "capability_not_found",
            "computer-use capability is not registered",
        )),
    }
}

fn parse_screenshot_arguments(
    arguments: Map<String, Value>,
) -> Result<ScreenshotArguments, RemoteControlError> {
    serde_json::from_value(Value::Object(arguments)).map_err(|error| {
        RemoteControlError::new(
            "invalid_arguments",
            format!("invalid screenshot arguments: {error}"),
        )
    })
}

fn parse_input_arguments(
    arguments: Map<String, Value>,
) -> Result<InputArguments, RemoteControlError> {
    let arguments: InputArguments =
        serde_json::from_value(Value::Object(arguments)).map_err(|error| {
            RemoteControlError::new(
                "invalid_arguments",
                format!("invalid input arguments: {error}"),
            )
        })?;
    validate_input_arguments(&arguments)?;
    Ok(arguments)
}

fn authorize_computer_use(
    policy: &RemoteControlPolicy,
    actor_account_key: &str,
    needs_input: bool,
) -> Result<(), RemoteControlError> {
    authorize(policy, actor_account_key)?;
    let allowed = if needs_input {
        policy.computer_use.allow_input
    } else {
        policy.computer_use.allow_screen_capture
    };
    if !allowed {
        return Err(RemoteControlError::new(
            "computer_use_denied",
            if needs_input {
                "computer input is disabled by the local Desktop policy"
            } else {
                "screen capture is disabled by the local Desktop policy"
            },
        ));
    }
    Ok(())
}

fn validate_input_arguments(arguments: &InputArguments) -> Result<(), RemoteControlError> {
    match arguments.action.as_str() {
        "move" => {
            require_coordinates(arguments)?;
            require_unused(arguments, false, false, false)?;
        }
        "click" => {
            require_coordinates(arguments)?;
            if !matches!(arguments.button.as_str(), "left" | "right" | "middle") {
                return invalid("button must be left, right, or middle");
            }
            if !(1..=2).contains(&arguments.clicks) {
                return invalid("clicks must be 1 or 2");
            }
            require_unused(arguments, false, false, false)?;
        }
        "scroll" => {
            optional_coordinates(arguments)?;
            if arguments.scroll_steps == 0 || arguments.scroll_steps.abs() > MAX_SCROLL_STEPS {
                return invalid("scrollSteps must be between -10 and 10, excluding 0");
            }
            require_unused(arguments, true, false, false)?;
        }
        "type" => {
            if arguments.text.is_empty() {
                return invalid("text must not be empty");
            }
            if arguments.text.encode_utf16().count() > MAX_TYPED_UTF16_UNITS {
                return invalid("text exceeds 2000 UTF-16 code units");
            }
            require_unused(arguments, false, true, false)?;
        }
        "key" => {
            if arguments.keys.is_empty() || arguments.keys.len() > MAX_HOTKEY_KEYS {
                return invalid("keys must contain between 1 and 8 keys");
            }
            for key in &arguments.keys {
                resolve_virtual_key(key)?;
            }
            require_unused(arguments, false, false, true)?;
        }
        _ => return invalid("action must be move, click, scroll, type, or key"),
    }
    Ok(())
}

fn require_coordinates(arguments: &InputArguments) -> Result<(), RemoteControlError> {
    if arguments.x.is_none() || arguments.y.is_none() {
        return invalid("x and y are required for this action");
    }
    optional_coordinates(arguments)
}

fn optional_coordinates(arguments: &InputArguments) -> Result<(), RemoteControlError> {
    if arguments.x.is_some() != arguments.y.is_some() {
        return invalid("x and y must be supplied together");
    }
    for (name, coordinate) in [("x", arguments.x), ("y", arguments.y)] {
        if let Some(value) = coordinate {
            if !(0..=NORMALIZED_COORDINATE_MAX).contains(&value) {
                return invalid(format!("{name} must be between 0 and 1000"));
            }
        }
    }
    Ok(())
}

fn require_unused(
    arguments: &InputArguments,
    allow_scroll: bool,
    allow_text: bool,
    allow_keys: bool,
) -> Result<(), RemoteControlError> {
    if !allow_scroll && arguments.scroll_steps != 0 {
        return invalid("scrollSteps is not valid for this action");
    }
    if !allow_text && !arguments.text.is_empty() {
        return invalid("text is not valid for this action");
    }
    if !allow_keys && !arguments.keys.is_empty() {
        return invalid("keys is not valid for this action");
    }
    Ok(())
}

fn invalid<T>(message: impl Into<String>) -> Result<T, RemoteControlError> {
    Err(RemoteControlError::new("invalid_arguments", message))
}

#[cfg(windows)]
fn capture_desktop() -> Result<Value, RemoteControlError> {
    use image::codecs::jpeg::JpegEncoder;
    use image::imageops::FilterType;
    use image::{DynamicImage, ImageBuffer, Rgb};
    use windows_sys::Win32::Graphics::Gdi::{
        BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC,
        GetDIBits, ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, CAPTUREBLT,
        DIB_RGB_COLORS, SRCCOPY,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN,
        SM_YVIRTUALSCREEN,
    };

    let left = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
    let top = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
    let width = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) };
    let height = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) };
    if width <= 0 || height <= 0 {
        return Err(RemoteControlError::new(
            "screen_capture_failed",
            "Windows reported an invalid virtual desktop size",
        ));
    }
    let pixel_count = (width as usize)
        .checked_mul(height as usize)
        .and_then(|value| value.checked_mul(4))
        .ok_or_else(|| {
            RemoteControlError::new("screen_capture_failed", "desktop dimensions overflow")
        })?;

    let screen_dc = unsafe { GetDC(std::ptr::null_mut()) };
    if screen_dc.is_null() {
        return Err(RemoteControlError::new(
            "screen_capture_failed",
            "GetDC failed",
        ));
    }
    let memory_dc = unsafe { CreateCompatibleDC(screen_dc) };
    if memory_dc.is_null() {
        unsafe { ReleaseDC(std::ptr::null_mut(), screen_dc) };
        return Err(RemoteControlError::new(
            "screen_capture_failed",
            "CreateCompatibleDC failed",
        ));
    }
    let bitmap = unsafe { CreateCompatibleBitmap(screen_dc, width, height) };
    if bitmap.is_null() {
        unsafe {
            DeleteDC(memory_dc);
            ReleaseDC(std::ptr::null_mut(), screen_dc);
        }
        return Err(RemoteControlError::new(
            "screen_capture_failed",
            "CreateCompatibleBitmap failed",
        ));
    }
    let previous = unsafe { SelectObject(memory_dc, bitmap) };

    let captured = unsafe {
        BitBlt(
            memory_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            left,
            top,
            SRCCOPY | CAPTUREBLT,
        )
    };
    if captured == 0 {
        unsafe {
            SelectObject(memory_dc, previous);
            DeleteObject(bitmap);
            DeleteDC(memory_dc);
            ReleaseDC(std::ptr::null_mut(), screen_dc);
        }
        return Err(RemoteControlError::new(
            "screen_capture_failed",
            "BitBlt failed",
        ));
    }

    let mut bgra = vec![0_u8; pixel_count];
    let mut info = BITMAPINFO {
        bmiHeader: BITMAPINFOHEADER {
            biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: width,
            biHeight: -height,
            biPlanes: 1,
            biBitCount: 32,
            biCompression: BI_RGB,
            biSizeImage: pixel_count as u32,
            ..Default::default()
        },
        ..Default::default()
    };
    let scanlines = unsafe {
        GetDIBits(
            memory_dc,
            bitmap,
            0,
            height as u32,
            bgra.as_mut_ptr().cast(),
            &mut info,
            DIB_RGB_COLORS,
        )
    };
    unsafe {
        SelectObject(memory_dc, previous);
        DeleteObject(bitmap);
        DeleteDC(memory_dc);
        ReleaseDC(std::ptr::null_mut(), screen_dc);
    }
    if scanlines != height {
        return Err(RemoteControlError::new(
            "screen_capture_failed",
            "GetDIBits returned an incomplete frame",
        ));
    }

    let mut rgb = Vec::with_capacity((width as usize) * (height as usize) * 3);
    for pixel in bgra.chunks_exact(4) {
        rgb.extend_from_slice(&[pixel[2], pixel[1], pixel[0]]);
    }
    let image =
        ImageBuffer::<Rgb<u8>, _>::from_raw(width as u32, height as u32, rgb).ok_or_else(|| {
            RemoteControlError::new("screen_capture_failed", "could not build image buffer")
        })?;
    let image = DynamicImage::ImageRgb8(image).resize(
        MAX_CAPTURE_DIMENSION,
        MAX_CAPTURE_DIMENSION,
        FilterType::Triangle,
    );
    let image_width = image.width();
    let image_height = image.height();
    let mut jpeg = Vec::new();
    JpegEncoder::new_with_quality(&mut jpeg, 78)
        .encode_image(&image)
        .map_err(|error| {
            RemoteControlError::new(
                "screen_capture_failed",
                format!("JPEG encoding failed: {error}"),
            )
        })?;

    Ok(json!({
        "mimeType": "image/jpeg",
        "data": base64::engine::general_purpose::STANDARD.encode(jpeg),
        "imageWidth": image_width,
        "imageHeight": image_height,
        "coordinateSpace": {
            "type": "normalized",
            "minimum": 0,
            "maximum": NORMALIZED_COORDINATE_MAX,
            "origin": "top_left"
        },
        "virtualScreen": {
            "left": left,
            "top": top,
            "width": width,
            "height": height
        },
        "capturedAtMs": SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64
    }))
}

#[cfg(not(windows))]
fn capture_desktop() -> Result<Value, RemoteControlError> {
    Err(RemoteControlError::new(
        "platform_unsupported",
        "visual computer use is currently implemented for Windows only",
    ))
}

#[cfg(windows)]
fn apply_input(arguments: InputArguments) -> Result<Value, RemoteControlError> {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBDINPUT, KEYEVENTF_EXTENDEDKEY,
        KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
        MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP,
        MOUSEEVENTF_WHEEL, MOUSEINPUT,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, SetCursorPos, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN,
        SM_YVIRTUALSCREEN,
    };

    fn send(inputs: &[INPUT]) -> Result<(), RemoteControlError> {
        let sent = unsafe {
            SendInput(
                inputs.len() as u32,
                inputs.as_ptr(),
                std::mem::size_of::<INPUT>() as i32,
            )
        };
        if sent != inputs.len() as u32 {
            return Err(RemoteControlError::new(
                "input_failed",
                "SendInput did not inject every requested event",
            ));
        }
        Ok(())
    }

    fn mouse(flags: u32, data: u32) -> INPUT {
        INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    mouseData: data,
                    dwFlags: flags,
                    ..Default::default()
                },
            },
        }
    }

    fn keyboard(vk: u16, scan: u16, flags: u32) -> INPUT {
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: vk,
                    wScan: scan,
                    dwFlags: flags,
                    ..Default::default()
                },
            },
        }
    }

    let move_pointer = |x: i32, y: i32| -> Result<(i32, i32), RemoteControlError> {
        let left = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
        let top = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
        let width = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) };
        let height = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) };
        if width <= 0 || height <= 0 {
            return Err(RemoteControlError::new(
                "input_failed",
                "Windows reported an invalid virtual desktop size",
            ));
        }
        let screen_x = left + (x * (width - 1) + 500) / NORMALIZED_COORDINATE_MAX;
        let screen_y = top + (y * (height - 1) + 500) / NORMALIZED_COORDINATE_MAX;
        if unsafe { SetCursorPos(screen_x, screen_y) } == 0 {
            return Err(RemoteControlError::new(
                "input_failed",
                "SetCursorPos failed",
            ));
        }
        Ok((screen_x, screen_y))
    };

    let mut cursor = None;
    if let (Some(x), Some(y)) = (arguments.x, arguments.y) {
        cursor = Some(move_pointer(x, y)?);
    }

    match arguments.action.as_str() {
        "move" => {}
        "click" => {
            let (down, up) = match arguments.button.as_str() {
                "left" => (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
                "right" => (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
                "middle" => (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
                _ => unreachable!("validated mouse button"),
            };
            let mut inputs = Vec::with_capacity(arguments.clicks as usize * 2);
            for _ in 0..arguments.clicks {
                inputs.push(mouse(down, 0));
                inputs.push(mouse(up, 0));
            }
            send(&inputs)?;
        }
        "scroll" => {
            let delta = arguments.scroll_steps.saturating_mul(120);
            send(&[mouse(MOUSEEVENTF_WHEEL, delta as u32)])?;
        }
        "type" => {
            let mut inputs = Vec::with_capacity(arguments.text.encode_utf16().count() * 2);
            for unit in arguments.text.encode_utf16() {
                inputs.push(keyboard(0, unit, KEYEVENTF_UNICODE));
                inputs.push(keyboard(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));
            }
            send(&inputs)?;
        }
        "key" => {
            let resolved = arguments
                .keys
                .iter()
                .map(|key| resolve_virtual_key(key))
                .collect::<Result<Vec<_>, _>>()?;
            let mut inputs = Vec::with_capacity(resolved.len() * 2);
            for &(vk, extended) in &resolved {
                inputs.push(keyboard(
                    vk,
                    0,
                    if extended { KEYEVENTF_EXTENDEDKEY } else { 0 },
                ));
            }
            for &(vk, extended) in resolved.iter().rev() {
                inputs.push(keyboard(
                    vk,
                    0,
                    KEYEVENTF_KEYUP | if extended { KEYEVENTF_EXTENDEDKEY } else { 0 },
                ));
            }
            send(&inputs)?;
        }
        _ => unreachable!("validated action"),
    }

    Ok(json!({
        "applied": true,
        "action": arguments.action,
        "normalizedPosition": arguments.x.zip(arguments.y).map(|(x, y)| json!({"x": x, "y": y})),
        "screenPosition": cursor.map(|(x, y)| json!({"x": x, "y": y}))
    }))
}

#[cfg(not(windows))]
fn apply_input(_arguments: InputArguments) -> Result<Value, RemoteControlError> {
    Err(RemoteControlError::new(
        "platform_unsupported",
        "visual computer use is currently implemented for Windows only",
    ))
}

fn resolve_virtual_key(key: &str) -> Result<(u16, bool), RemoteControlError> {
    let normalized = key.trim().to_ascii_uppercase();
    let resolved = match normalized.as_str() {
        "CTRL" | "CONTROL" => (0x11, false),
        "ALT" => (0x12, false),
        "SHIFT" => (0x10, false),
        "WIN" | "META" => (0x5B, true),
        "ENTER" | "RETURN" => (0x0D, false),
        "TAB" => (0x09, false),
        "ESC" | "ESCAPE" => (0x1B, false),
        "BACKSPACE" => (0x08, false),
        "DELETE" => (0x2E, true),
        "SPACE" => (0x20, false),
        "LEFT" => (0x25, true),
        "UP" => (0x26, true),
        "RIGHT" => (0x27, true),
        "DOWN" => (0x28, true),
        "HOME" => (0x24, true),
        "END" => (0x23, true),
        "PAGEUP" => (0x21, true),
        "PAGEDOWN" => (0x22, true),
        "F1" => (0x70, false),
        "F2" => (0x71, false),
        "F3" => (0x72, false),
        "F4" => (0x73, false),
        "F5" => (0x74, false),
        "F6" => (0x75, false),
        "F7" => (0x76, false),
        "F8" => (0x77, false),
        "F9" => (0x78, false),
        "F10" => (0x79, false),
        "F11" => (0x7A, false),
        "F12" => (0x7B, false),
        _ if normalized.len() == 1 => {
            let byte = normalized.as_bytes()[0];
            if byte.is_ascii_alphanumeric() {
                (byte as u16, false)
            } else {
                return invalid(format!("unsupported key: {key}"));
            }
        }
        _ => return invalid(format!("unsupported key: {key}")),
    };
    Ok(resolved)
}

#[cfg(test)]
mod tests {
    use super::{parse_input_arguments, InputArguments};
    use serde_json::{json, Map, Value};

    fn parse(value: Value) -> Result<InputArguments, crate::remote_control::RemoteControlError> {
        let Value::Object(arguments) = value else {
            return parse_input_arguments(Map::new());
        };
        parse_input_arguments(arguments)
    }

    #[test]
    fn accepts_normalized_click_and_unicode_typing() {
        assert!(parse(json!({
            "actorAccountKey": "milky:1",
            "action": "click",
            "x": 500,
            "y": 250,
            "button": "left",
            "clicks": 2
        }))
        .is_ok());
        assert!(parse(json!({
            "actorAccountKey": "milky:1",
            "action": "type",
            "text": "你好"
        }))
        .is_ok());
    }

    #[test]
    fn rejects_out_of_range_and_mixed_action_fields() {
        assert_eq!(
            parse(json!({
                "actorAccountKey": "milky:1",
                "action": "click",
                "x": 1001,
                "y": 0
            }))
            .unwrap_err()
            .code,
            "invalid_arguments"
        );
        assert!(parse(json!({
            "actorAccountKey": "milky:1",
            "action": "type",
            "text": "hello",
            "keys": ["ENTER"]
        }))
        .is_err());
    }

    #[test]
    #[cfg(windows)]
    #[ignore = "requires an interactive Windows desktop"]
    fn captures_a_bounded_visual_frame() {
        let frame = super::capture_desktop().unwrap();
        assert_eq!(frame["mimeType"], "image/jpeg");
        assert!(frame["imageWidth"].as_u64().unwrap() <= 1_600);
        assert!(frame["imageHeight"].as_u64().unwrap() <= 1_600);
        assert!(frame["data"].as_str().unwrap().len() > 1_000);
    }
}
