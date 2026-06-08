#[tauri::command]
fn runtime_mode() -> &'static str {
    "mock"
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![runtime_mode])
        .run(tauri::generate_context!())
        .expect("failed to run Nahida Desktop");
}
