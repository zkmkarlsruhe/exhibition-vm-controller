; ==============================================================================
; Idle Monitor Script
; Author: Marc Schütze
; Organization: ZKM | Center for Art and Media Karlsruhe
; License: MIT
;
; Purpose: Monitors system idle time and triggers VM restart after configured
;          period of inactivity. Useful for exhibition environments where
;          interaction has stopped.
;
; Configuration: Change $host and $IDLE_TIME_THRESHOLD as needed
; ==============================================================================

#include <Timers.au3>
Opt("TrayIconHide", 1)  ; Hide tray icon

; ===== CONFIGURATION =====
Local $host = "192.168.122.1"  ; Host IP
Local $url = "http://" & $host & ":8000/api/v1/vm/restart"

; Idle time threshold (in milliseconds)
Global Const $IDLE_TIME_THRESHOLD = 15 * 60 * 1000  ; 15 minutes

; ===== FUNCTIONS =====

; Wait for network connectivity
Func WaitForNetwork()
    ConsoleWrite("Waiting for network connectivity to " & $host & "..." & @CRLF)
    While Ping($host, 1000) = 0
        ConsoleWrite("Network not ready, retrying..." & @CRLF)
        Sleep(1000)
    WEnd
    ConsoleWrite("Network is ready." & @CRLF)
EndFunc

; ===== MAIN LOOP =====
ConsoleWrite("==================================================" & @CRLF)
ConsoleWrite("Idle Monitor Script" & @CRLF)
ConsoleWrite("Idle threshold: " & ($IDLE_TIME_THRESHOLD / 1000) & " seconds (" & ($IDLE_TIME_THRESHOLD / 60000) & " minutes)" & @CRLF)
ConsoleWrite("==================================================" & @CRLF)

While 1
    ; Get system idle time using Windows API
    Local $idleTime = _Timer_GetIdleTime()
    ConsoleWrite("Current idle time: " & $idleTime & " ms (" & Round($idleTime / 1000, 1) & " seconds)" & @CRLF)

    ; Check if idle time exceeds threshold
    If $idleTime >= $IDLE_TIME_THRESHOLD Then
        ConsoleWrite("==================================================" & @CRLF)
        ConsoleWrite("IDLE THRESHOLD EXCEEDED!" & @CRLF)
        ConsoleWrite("Triggering VM restart..." & @CRLF)
        ConsoleWrite("==================================================" & @CRLF)

        ; Wait for network
        WaitForNetwork()

        ; Trigger restart via host API
        InetRead($url)
        ConsoleWrite("Restart request sent. Exiting..." & @CRLF)

        ; Exit script (will restart with VM)
        Exit
    Else
        ConsoleWrite("Idle time below threshold. No action needed." & @CRLF)
    EndIf

    ; Check every 5 seconds
    Sleep(5000)
WEnd
