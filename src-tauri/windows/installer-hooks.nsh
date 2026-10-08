; The Windows server is a one-folder build under $INSTDIR\riviu-server (see
; tauri.windows.conf.json). Tauri's installer only knows the files of the build
; it ships, so clean that folder explicitly.

; An update is copied over the existing install without uninstalling first.
; Remove the previous server so files the new build no longer ships (an old
; Chromium revision, stale .pyd or dist-info folders) do not pile up or get
; imported. The updater stops the server in its pre-exit hook
; (src-tauri/src/lib.rs) before it starts this installer. A manual install keeps
; the default behaviour, because the app may still be running then.
!macro NSIS_HOOK_PREINSTALL
  ${If} $UpdateMode = 1
    RMDir /r "$INSTDIR\riviu-server"
  ${EndIf}
!macroend

; The uninstaller deletes only the files it installed. Also remove what the
; server created at runtime (Chromium writes debug.log next to its executable),
; which would otherwise keep the install folder behind. This runs after the
; uninstaller has checked that the app is closed.
!macro NSIS_HOOK_POSTUNINSTALL
  RMDir /r "$INSTDIR\riviu-server"
  RMDir "$INSTDIR"
!macroend
