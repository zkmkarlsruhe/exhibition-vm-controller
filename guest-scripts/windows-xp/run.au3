; ==============================================================================
; Monitoring Scripts Restart Utility
; Author: Marc Schütze
; Organization: ZKM | Center for Art and Media Karlsruhe
; License: MIT
;
; Purpose: Restarts all monitoring scripts. Useful for:
;          - Manual recovery
;          - Testing
;          - Called from other scripts if needed
;
; Configuration: Update $processes array to match your scripts
; ==============================================================================

; ===== CONFIGURATION =====

; List of monitoring scripts to restart
; Adjust to match your compiled script names
Local $processes[3] = ["heartbeat.exe", "idle-monitor.exe", "process-watchdog.exe"]

; Optionally add button-detector.exe if you use it:
; Local $processes[4] = ["heartbeat.exe", "idle-monitor.exe", "process-watchdog.exe", "button-detector.exe"]

; ===== MAIN SCRIPT =====

ConsoleWrite("==================================================" & @CRLF)
ConsoleWrite("Restarting Monitoring Scripts" & @CRLF)
ConsoleWrite("==================================================" & @CRLF)

; Close all processes
ConsoleWrite("Stopping processes..." & @CRLF)
For $i = 0 To UBound($processes) - 1
    ConsoleWrite("  Stopping: " & $processes[$i] & @CRLF)
    ProcessClose($processes[$i])
Next

; Wait for processes to fully terminate
ConsoleWrite("Waiting 5 seconds for processes to terminate..." & @CRLF)
Sleep(5000)

; Restart all processes
ConsoleWrite("Starting processes..." & @CRLF)
For $i = 0 To UBound($processes) - 1
    ConsoleWrite("  Starting: " & $processes[$i] & @CRLF)
    Run($processes[$i])
    Sleep(500)  ; Small delay between starts
Next

ConsoleWrite("==================================================" & @CRLF)
ConsoleWrite("All monitoring scripts restarted successfully!" & @CRLF)
ConsoleWrite("==================================================" & @CRLF)
