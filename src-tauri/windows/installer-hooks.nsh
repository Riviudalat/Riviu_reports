; The Windows server is a one-folder build under $INSTDIR\riviu-server (see
; tauri.windows.conf.json). Tauri's installer only knows the files of the build
; it ships, so clean that folder explicitly.

; Stop server processes left running from this install folder: the old one-file
; $INSTDIR\riviu-server.exe (releases up to 0.1.17 did not stop it before an
; update) and anything under $INSTDIR\riviu-server (the one-folder server and
; its bundled Chromium), for example after the app itself was force-closed.
; Only processes whose executable is inside $INSTDIR are selected, never a
; process matched by image name alone. Each one is killed with its process tree
; (`taskkill /T`, as lib.rs does), because Chromium's sandboxed child processes
; do not expose their executable path and would otherwise keep files locked.
; A one-file server is a bootloader parent plus a Python child; killing the child
; first lets the parent delete its %TEMP%\_MEI* folder (about 1 GB) before it
; exits, so wait up to 10 s for that before killing what is left, then wait up
; to 10 s for everything to exit. $INSTDIR goes through an environment variable
; so no quoting can break the command.
!macro RIVIU_STOP_INSTALLED_SERVERS
  System::Call 'Kernel32::SetEnvironmentVariable(t "RIVIU_INSTDIR", t "$INSTDIR")i'
  nsExec::Exec `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$$r=[IO.Path]::GetFullPath($$env:RIVIU_INSTDIR).TrimEnd('\')+'\';$$x=$$r+'riviu-server.exe';$$d=$$r+'riviu-server\';function S{@(Get-CimInstance Win32_Process|?{$$p=$$_.ExecutablePath;$$p -and ($$p -eq $$x -or $$p.StartsWith($$d,'OrdinalIgnoreCase'))})};function K($$q){foreach($$o in $$q){taskkill.exe /PID $$o.ProcessId /T /F *>$$null}};function W($$f){for($$i=0;$$i -lt 40 -and @(&$$f).Count;$$i++){Start-Sleep -m 250}};$$a=S;$$k=@($$a|?{$$_.ExecutablePath -eq $$x}|%{$$_.ProcessId});K @($$a|?{$$k -contains $$_.ParentProcessId});W {S|?{$$_.ExecutablePath -eq $$x}};K (S);W {S}"`
  Pop $0
  System::Call 'Kernel32::SetEnvironmentVariable(t "RIVIU_INSTDIR", p 0)i'
!macroend

; Tauri's installer checks for a running app only after this hook. Run that check
; first (the second check then finds nothing), so a server is never stopped
; under a running app and the user can still cancel. An update is copied over
; the existing install without uninstalling first, so remove the previous server
; build: files the new build no longer ships (an old Chromium revision, stale .pyd
; or dist-info folders) must not pile up or get imported. Releases up to 0.1.17
; shipped a one-file $INSTDIR\riviu-server.exe that no newer build replaces.
!macro NSIS_HOOK_PREINSTALL
  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
  !insertmacro RIVIU_STOP_INSTALLED_SERVERS
  RMDir /r "$INSTDIR\riviu-server"
  Delete "$INSTDIR\riviu-server.exe"
!macroend

; Same order for the uninstaller: once the app is closed, stop any server it left
; behind so its files are not locked.
!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
  !insertmacro RIVIU_STOP_INSTALLED_SERVERS
!macroend

; The uninstaller deletes only the files it installed. Also remove what the
; server created at runtime (Chromium writes debug.log next to its executable),
; which would otherwise keep the install folder behind.
!macro NSIS_HOOK_POSTUNINSTALL
  RMDir /r "$INSTDIR\riviu-server"
  Delete "$INSTDIR\riviu-server.exe"
  RMDir "$INSTDIR"
!macroend
