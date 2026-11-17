; The app's daemon may still be shutting down when the installer starts (the updater launches it at once), and a
; running exe can't be overwritten ("Error opening file for writing"). Stop it, with its Claude processes, first.
!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /T /IM switchboard-daemon.exe'
  Sleep 500
!macroend
