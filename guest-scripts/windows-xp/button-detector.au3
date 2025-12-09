; ==============================================================================
; Button Detector Script (EXAMPLE)
; Author: Marc Schütze
; Organization: ZKM | Center for Art and Media Karlsruhe
; License: MIT
;
; Purpose: Example of application-specific monitoring. Detects a button
;          on screen by pixel color and clicks it when host API signals ready.
;
; Use Case: Physical hardware button (Arduino, etc.) connected to host.
;           Host receives hardware signal and sets API flag. This script
;           performs the actual UI interaction in the guest.
;
; Configuration: Adjust coordinates, colors, and API endpoint
; ==============================================================================

; ===== CONFIGURATION =====

; Host configuration
Local $host = "192.168.122.1"
Local $apiUrl = "http://" & $host & ":8000/api/v1/button-status"

; Button coordinates (adjust for your application)
Local $buttonX = @DesktopWidth / 2   ; Center of screen horizontally
Local $buttonY = 100                  ; 100 pixels from top

; Expected button color (RGB)
; Use AutoIT Window Info tool to get exact color
Local $buttonColorR = 246
Local $buttonColorG = 246
Local $buttonColorB = 243

; Check interval (milliseconds)
Local $checkInterval = 500  ; Check every 500ms

; ===== FUNCTIONS =====

; Wait for network connectivity
Func WaitForNetwork()
    While Ping($host, 1000) = 0
        ConsoleWrite("Waiting for network..." & @CRLF)
        Sleep(1000)
    WEnd
EndFunc

; Check if button exists at coordinates by pixel color
Func CheckButtonPresent()
    ConsoleWrite("Checking for button at (" & $buttonX & "," & $buttonY & ")" & @CRLF)

    Local $pixelColor = PixelGetColor($buttonX, $buttonY)

    ; Convert to RGB
    Local $red = BitShift(BitAND($pixelColor, 0xFF0000), 16)
    Local $green = BitShift(BitAND($pixelColor, 0x00FF00), 8)
    Local $blue = BitAND($pixelColor, 0x0000FF)

    ConsoleWrite("  Pixel RGB: R=" & $red & " G=" & $green & " B=" & $blue & @CRLF)

    ; Check if color matches button
    If $red == $buttonColorR And $green == $buttonColorG And $blue == $buttonColorB Then
        ConsoleWrite("  Button detected!" & @CRLF)
        Return True
    Else
        ConsoleWrite("  Button not detected." & @CRLF)
        Return False
    EndIf
EndFunc

; Poll API to check if button should be clicked
Func CheckButtonStatus()
    ConsoleWrite("Polling API: " & $apiUrl & @CRLF)

    Local $oHTTP = ObjCreate("WinHttp.WinHttpRequest.5.1")
    If @error Then
        ConsoleWrite("  ERROR: Could not create HTTP object" & @CRLF)
        Return False
    EndIf

    $oHTTP.Open("GET", $apiUrl, False)
    $oHTTP.Send()

    If @error Then
        ConsoleWrite("  ERROR: Failed to send request" & @CRLF)
        Return False
    EndIf

    Local $response = $oHTTP.ResponseText
    ConsoleWrite("  API Response: " & $response & @CRLF)

    ; Check if response indicates button should be clicked
    ; Adjust this based on your API response format
    If StringInStr($response, '"pressed":true') Or StringInStr($response, '"pressed": true') Then
        ConsoleWrite("  API signals: Click button!" & @CRLF)
        Return True
    Else
        ConsoleWrite("  API signals: Wait..." & @CRLF)
        Return False
    EndIf
EndFunc

; Click the button
Func ClickButton()
    ConsoleWrite("Clicking button at (" & $buttonX & "," & $buttonY & ")" & @CRLF)
    MouseClick("left", $buttonX, $buttonY, 1, 0)  ; Instant click
    ConsoleWrite("Button clicked!" & @CRLF)
EndFunc

; ===== MAIN LOOP =====

ConsoleWrite("==================================================" & @CRLF)
ConsoleWrite("Button Detector Script" & @CRLF)
ConsoleWrite("Button position: (" & $buttonX & "," & $buttonY & ")" & @CRLF)
ConsoleWrite("Expected RGB: (" & $buttonColorR & "," & $buttonColorG & "," & $buttonColorB & ")" & @CRLF)
ConsoleWrite("API: " & $apiUrl & @CRLF)
ConsoleWrite("==================================================" & @CRLF)

While True
    WaitForNetwork()

    ; Check if button is present on screen
    If CheckButtonPresent() Then
        ; Check if API signals to click
        If CheckButtonStatus() Then
            ClickButton()
        EndIf
    Else
        ConsoleWrite("Button not visible, skipping API check" & @CRLF)
    EndIf

    ConsoleWrite("Sleeping for " & $checkInterval & "ms..." & @CRLF & @CRLF)
    Sleep($checkInterval)
WEnd
