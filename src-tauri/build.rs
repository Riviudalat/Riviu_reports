fn main() {
    let manifest =
        tauri_build::AppManifest::new().commands(&["check_for_update", "install_update"]);
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(manifest))
        .expect("failed to build desktop permissions");
}
