; ==============================================================================
; Heartbeat Monitor Script
; Author: Marc Schütze
; Organization: ZKM | Center for Art and Media Karlsruhe
; License: MIT
;
; Purpose: Sends periodic heartbeat signals to host controller to prove VM
;          is alive and functioning. Also verifies other monitoring scripts
;          are running.
;
; Configuration: Change $host to match your host IP address
; ==============================================================================

; ===== CONFIGURATION =====
Local $host = "192.168.122.1"  ; Host IP (typically libvirt gateway)
Local $url = "http://" & $host & ":8000/api/v1/heartbeat"

; Processes to verify are running (adjust to match your compiled scripts)
Local $processes[2] = ["process-watchdog.exe", "idle-monitor.exe"]

; ===== FUNCTIONS =====

; Wait for network connectivity before starting
Func WaitForNetwork()
    ConsoleWrite("Waiting for network connectivity to " & $host & "..." & @CRLF)
    While Ping($host, 1000) = 0
        ConsoleWrite("Network not ready, retrying..." & @CRLF)
        Sleep(1000)
    WEnd
    ConsoleWrite("Network is ready." & @CRLF)
EndFunc

; Send heartbeat to host controller
Func SendHeartbeat()
    ConsoleWrite("Sending heartbeat to: " & $url & @CRLF)

    ; Check if all required processes are running
    For $i = 0 To UBound($processes) - 1
        Local $processName = $processes[$i]
        Local $processExists = ProcessExists($processName)
        ConsoleWrite("  Process " & $processName & ": " & ($processExists ? "Running" : "NOT RUNNING") & @CRLF)

        ; If any critical process is missing, exit (triggers VM restart)
        If $processExists = 0 Then
            ConsoleWrite("CRITICAL: " & $processName & " is not running. Exiting..." & @CRLF)
            Exit
        EndIf
    Next

    ; Send heartbeat via HTTP GET
    Local $oHTTP = ObjCreate("WinHttp.WinHttpRequest.5.1")
    If @error Then
        ConsoleWrite("ERROR: Could not create HTTP object" & @CRLF)
        Return
    EndIf

    $oHTTP.Open("GET", $url, False)
    $oHTTP.Send()

    If @error Then
        ConsoleWrite("ERROR: Failed to send heartbeat" & @CRLF)
    Else
        ConsoleWrite("Heartbeat sent successfully" & @CRLF)
    EndIf
EndFunc

; ===== MAIN LOOP =====
ConsoleWrite("==================================================" & @CRLF)
ConsoleWrite("Heartbeat Monitor Script" & @CRLF)
ConsoleWrite("Host: " & $host & @CRLF)
ConsoleWrite("Endpoint: " & $url & @CRLF)
ConsoleWrite("==================================================" & @CRLF)

; Wait for network to be ready
WaitForNetwork()

; Main heartbeat loop
While True
    SendHeartbeat()
    ConsoleWrite("Sleeping for 1 second before next heartbeat..." & @CRLF & @CRLF)
    Sleep(1000)  ; Send heartbeat every 1 second
WEnd
