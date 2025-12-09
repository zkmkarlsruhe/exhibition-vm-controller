; ==============================================================================
; Process Watchdog Script (TEMPLATE)
; Author: Marc Schütze
; Organization: ZKM | Center for Art and Media Karlsruhe
; License: MIT
;
; Purpose: Monitors your artwork application, keeps it focused, closes
;          unauthorized windows, and triggers restart if application closes.
;
; IMPORTANT: This is a template. You MUST customize it for your application!
;
; Configuration: See CONFIGURATION section below
; ==============================================================================

; ===== CONFIGURATION - CUSTOMIZE FOR YOUR APPLICATION =====

; Path to your application executable
Local $applicationPath = "C:\Path\To\Your\Application.exe"

; OR if web-based, use Internet Explorer:
; Local $applicationPath = "C:\Program Files\Internet Explorer\IEXPLORE.EXE"
; Local $applicationUrl = "http://your-application-url"

; Expected window title (or substring) of your application
Local $expectedWindowTitle = "Your Application"

; Host configuration
Local $host = "192.168.122.1"
Local $restartUrl = "http://" & $host & ":8000/api/v1/vm/restart"

; Windows that are allowed to remain open
; Add your application window titles and any system windows you want to keep
Global $allowedWindows[5] = [ _
    "Your Application", _
    "SciTE", _           ; AutoIT editor (remove if not needed)
    "Program Manager", _ ; Desktop
    "Your App Window", _ ; Additional window titles
    "Windows Task Manager" _
]

; Debug mode: Set to True to see what would be closed without actually closing
Global $debug = False

; Maximum wait time for application window to appear (seconds)
Global $maxWaitTime = 20

; ===== FUNCTIONS =====

; Wait for network connectivity
Func WaitForNetwork()
    ConsoleWrite("Waiting for network connectivity to " & $host & "..." & @CRLF)
    While Ping($host, 1000) = 0
        Sleep(1000)
    WEnd
    ConsoleWrite("Network is ready." & @CRLF)
EndFunc

; Check if a window handle is visible
Func IsVisible($handle)
    Return BitAND(WinGetState($handle), 2) <> 0
EndFunc

; Check if application window is open
Func IsApplicationWindowOpen()
    Local $windowList = WinList()
    For $i = 1 To $windowList[0][0]
        Local $title = $windowList[$i][0]
        If StringInStr($title, $expectedWindowTitle) Then
            Return True
        EndIf
    Next
    Return False
EndFunc

; Focus and maximize the application window
Func FocusApplicationWindow()
    Local $windowList = WinList()
    For $i = 1 To $windowList[0][0]
        Local $title = $windowList[$i][0]
        If StringInStr($title, $expectedWindowTitle) Then
            Local $state = WinGetState($title, "")

            ; Restore if minimized
            If BitAND($state, 16) Then
                WinSetState($title, "", @SW_RESTORE)
                ConsoleWrite("Restored window: " & $title & @CRLF)
            EndIf

            ; Maximize if not maximized
            If Not BitAND($state, 32) Then
                WinSetState($title, "", @SW_MAXIMIZE)
                ConsoleWrite("Maximized window: " & $title & @CRLF)
            EndIf

            ; Activate (bring to front)
            WinActivate($title)
            ExitLoop
        EndIf
    Next
EndFunc

; Close windows that are not in the allowed list
Func CloseUnauthorizedWindows()
    Local $windowList = WinList()

    For $i = 1 To $windowList[0][0]
        If WinExists($windowList[$i][0]) And IsVisible($windowList[$i][1]) Then
            Local $title = $windowList[$i][0]

            ; Skip windows with no title
            If $title = "" Then
                ContinueLoop
            EndIf

            ; Check if window is in allowed list
            Local $keepWindow = False
            For $j = 0 To UBound($allowedWindows) - 1
                If StringLeft($title, StringLen($allowedWindows[$j])) = $allowedWindows[$j] Then
                    $keepWindow = True
                    ExitLoop
                EndIf
            Next

            ; Close if not allowed
            If Not $keepWindow Then
                If $debug Then
                    ConsoleWrite("[DEBUG] Would close window: " & $title & @CRLF)
                Else
                    WinClose($title)
                    ConsoleWrite("Closed unauthorized window: " & $title & @CRLF)
                EndIf
            EndIf
        EndIf
    Next
EndFunc

; ===== MAIN SCRIPT =====

ConsoleWrite("==================================================" & @CRLF)
ConsoleWrite("Process Watchdog Script" & @CRLF)
ConsoleWrite("Application: " & $applicationPath & @CRLF)
ConsoleWrite("Expected window: " & $expectedWindowTitle & @CRLF)
ConsoleWrite("Debug mode: " & ($debug ? "ON" : "OFF") & @CRLF)
ConsoleWrite("==================================================" & @CRLF)

; Move mouse out of the way
MouseMove(0, 0, 0)

; Close any existing instances
ProcessClose($applicationPath)
Sleep(1000)

; Launch application
ConsoleWrite("Launching application..." & @CRLF)

; For standalone executable:
Run($applicationPath)

; OR for web-based application (uncomment and adjust):
; Run($applicationPath & " " & $applicationUrl)

; Wait for application window to appear
Local $waitedTime = 0
Local $appWindowLoaded = False

While $waitedTime < $maxWaitTime
    Local $windowList = WinList()

    For $i = 1 To $windowList[0][0]
        Local $title = $windowList[$i][0]
        If StringInStr($title, $expectedWindowTitle) Then
            $appWindowLoaded = True
            ConsoleWrite("Application window found: " & $title & @CRLF)
            ExitLoop
        EndIf
    Next

    If $appWindowLoaded Then ExitLoop

    Sleep(1000)
    $waitedTime += 1
WEnd

; Check if application loaded successfully
If Not $appWindowLoaded Then
    ConsoleWrite("WARNING: Application window not found within timeout!" & @CRLF)
    ConsoleWrite("Expected window title containing: " & $expectedWindowTitle & @CRLF)
    ; You might want to trigger a restart here
Else
    ConsoleWrite("Application loaded successfully." & @CRLF)

    ; Main monitoring loop
    While IsApplicationWindowOpen()
        FocusApplicationWindow()
        CloseUnauthorizedWindows()
        Sleep(1000)  ; Check every second
    WEnd
EndIf

; If we get here, application window was closed
ConsoleWrite("==================================================" & @CRLF)
ConsoleWrite("APPLICATION CLOSED DETECTED!" & @CRLF)
ConsoleWrite("Triggering VM restart..." & @CRLF)
ConsoleWrite("==================================================" & @CRLF)

; Trigger restart
InetRead($restartUrl)
ConsoleWrite("Restart request sent." & @CRLF)
