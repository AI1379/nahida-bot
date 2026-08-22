fn main() {
    // Tauri embeds the Windows application icon during the native build. The
    // generated resource file is otherwise cached by Cargo when only the icon
    // asset or Tauri configuration changes.
    println!("cargo:rerun-if-changed=icons/icon.ico");
    println!("cargo:rerun-if-changed=tauri.conf.json");
    tauri_build::build()
}
