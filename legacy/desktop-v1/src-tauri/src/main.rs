#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            println!("Anvesha startup complete: {}", app.package_info().name);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
