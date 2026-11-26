(*
Idle Monitor for Exhibition VM Controller
Mac OS 9 Guest Monitoring Scripts

Purpose: Detect user inactivity and trigger VM restart after prolonged idle time
Author: Exhibition VM Controller Project
License: MIT

This script monitors user activity (mouse/keyboard input) and triggers a VM
restart if no activity is detected for a configured period (default: 15 minutes).

This is useful for exhibition environments where:
- Visitors may leave the installation idle
- The system should reset to a fresh state after inactivity
- Content should be ready for the next visitor

IMPORTANT: This must be saved as a "Stay-Open Application" in Script Editor.

NOTE: Mac OS 9 has limited idle time detection capabilities. This script
uses a simple approach based on System Events. More sophisticated detection
may require additional scripting additions or extensions.

Setup:
1. Open this script in Script Editor
2. Modify configuration properties below
3. Save As... → File Format: Application
4. Check "Stay Open" option
5. Check "Never Show Startup Screen" option
6. Place in System Folder:Startup Items for automatic launch
*)

-- =======================
-- CONFIGURATION
-- =======================

-- Network settings
property hostIP : "192.168.122.1" -- Host controller IP address
property apiPort : "8000" -- API port number

-- Idle detection settings
property idleThresholdSeconds : 900 -- Idle timeout in seconds (default: 900 = 15 minutes)
property checkInterval : 5 -- Seconds between idle checks (default: 5)
property restartEndpoint : "/api/v1/vm/restart" -- API endpoint to trigger restart

-- Idle detection method
property useScreenSaver : true -- Use screen saver activation as idle indicator
property useLastActivityTime : false -- Use last activity time (requires additional tools)

-- Logging
property verboseLogging : true -- Enable detailed logging

-- Runtime state
property httpLib : missing value -- HTTP helper library reference
property lastActivityTime : missing value -- Timestamp of last detected activity
property idleCheckCount : 0 -- Number of idle checks performed
property screenSaverWasActive : false -- Track screen saver state changes

-- =======================
-- MAIN ENTRY POINT
-- =======================

(*
	Main initialization - called when script app launches.
*)
on run
	-- Display startup banner
	logMessage("========================================")
	logMessage("Exhibition VM Controller")
	logMessage("Idle Monitor for Mac OS 9")
	logMessage("========================================")
	logMessage("")

	-- Load HTTP helper library
	try
		set libPath to getLibraryPath()
		logMessage("Loading HTTP helper from: " & libPath)
		set httpLib to load script file libPath
	on error errMsg
		logMessage("FATAL ERROR: Cannot load HTTP helper library")
		logMessage("Error: " & errMsg)
		error "Cannot load HTTP helper library"
	end try

	-- Configure HTTP helper
	httpLib's setHostIP(hostIP)
	httpLib's setAPIPort(apiPort)

	-- Wait for network connectivity
	logMessage("Checking network connectivity...")
	httpLib's waitForNetwork()
	logMessage("Network is ready")
	logMessage("")

	-- Initialize HTTP helper
	logMessage("Initializing HTTP helper...")
	set httpReady to httpLib's initialize()

	if not httpReady then
		logMessage("FATAL ERROR: No HTTP method available")
		error "No HTTP method available"
	end if

	logMessage("HTTP helper initialized successfully")
	logMessage("")

	-- Initialize idle detection
	set lastActivityTime to current date
	logMessage("Idle detection initialized")
	logMessage("")

	-- Display configuration
	logMessage("Configuration:")
	logMessage("  Host: " & hostIP & ":" & apiPort)
	logMessage("  Idle threshold: " & idleThresholdSeconds & " seconds (" & (idleThresholdSeconds / 60) & " minutes)")
	logMessage("  Check interval: " & checkInterval & " seconds")
	logMessage("  Detection method: " & getDetectionMethodName())
	logMessage("")

	logMessage("Idle monitor is now running")
	logMessage("Press Cmd+Q to quit")
	logMessage("========================================")
	logMessage("")

	-- Idle handler will take over from here
end run

(*
	Idle handler - called repeatedly while script is running.

	Returns: Number of seconds to wait before calling idle again
*)
on idle
	try
		-- Increment check counter
		set idleCheckCount to idleCheckCount + 1

		-- Get current idle time
		set idleSeconds to getIdleTime()

		if verboseLogging and (idleCheckCount mod 12 = 0) then
			-- Log every minute (12 checks at 5-second intervals)
			logMessage("Idle check #" & idleCheckCount & ": " & idleSeconds & " seconds idle")
		end if

		-- Check if idle threshold exceeded
		if idleSeconds ≥ idleThresholdSeconds then
			logMessage("")
			logMessage("========================================")
			logMessage("IDLE THRESHOLD EXCEEDED")
			logMessage("Idle time: " & idleSeconds & " seconds")
			logMessage("Threshold: " & idleThresholdSeconds & " seconds")
			logMessage("Requesting VM restart...")
			logMessage("========================================")

			-- Request VM restart
			set success to httpLib's sendRequest(restartEndpoint)

			if success then
				logMessage("VM restart requested successfully")
			else
				logMessage("ERROR: Failed to request VM restart")
			end if

			-- Wait a moment then exit (VM will restart anyway)
			delay 5
			logMessage("Exiting idle monitor")
			error "Idle timeout - VM restarting"
		end if

	on error errMsg
		logMessage("ERROR in idle monitor: " & errMsg)
	end try

	-- Return interval before next idle call
	return checkInterval
end idle

(*
	Quit handler - called when script is quitting.
*)
on quit
	logMessage("")
	logMessage("========================================")
	logMessage("Idle monitor shutting down")
	logMessage("Total idle checks: " & idleCheckCount)
	logMessage("========================================")
	continue quit
end quit

-- =======================
-- IDLE DETECTION
-- =======================

(*
	Get the current idle time in seconds.

	Returns: Number of seconds since last user activity
*)
on getIdleTime()
	if useScreenSaver then
		return getIdleTimeFromScreenSaver()
	else if useLastActivityTime then
		return getIdleTimeFromLastActivity()
	else
		-- Fallback: assume not idle
		return 0
	end if
end getIdleTime

(*
	Detect idle time based on screen saver activation.

	This method checks if the screen saver is running. If it is, we consider
	the system idle. This is a simple but reliable method on Mac OS 9.

	Returns: idleThresholdSeconds if screen saver is active, 0 otherwise
*)
on getIdleTimeFromScreenSaver()
	try
		tell application "System Events"
			-- Check if screen saver process is running
			if exists process "Screen Saver" then
				-- Screen saver is active - system is idle
				if not screenSaverWasActive then
					logMessage("Screen saver activated - system is now idle")
					set screenSaverWasActive to true
				end if
				-- Return threshold time to trigger immediate restart
				return idleThresholdSeconds
			else
				-- Screen saver not active - system has activity
				if screenSaverWasActive then
					logMessage("Screen saver deactivated - activity resumed")
					set screenSaverWasActive to false
					set lastActivityTime to current date
				end if
				return 0
			end if
		end tell
	on error
		-- Can't detect screen saver, try Finder method
		try
			tell application "Finder"
				if exists application process "Screen Saver" then
					return idleThresholdSeconds
				else
					return 0
				end if
			end tell
		on error
			-- Can't detect - assume active
			return 0
		end try
	end try
end getIdleTimeFromScreenSaver

(*
	Detect idle time based on last recorded activity.

	This method tracks when we last saw the system active and calculates
	time elapsed. This is less accurate than using system APIs but works
	as a fallback.

	Returns: Seconds since last activity
*)
on getIdleTimeFromLastActivity()
	try
		-- Check if there's current activity
		set hasActivity to detectCurrentActivity()

		if hasActivity then
			-- Activity detected, reset timer
			set lastActivityTime to current date
			return 0
		else
			-- No activity, calculate time since last activity
			set now to current date
			set idleSeconds to (now - lastActivityTime)
			return idleSeconds
		end if
	on error
		-- Error detecting activity, assume not idle
		return 0
	end try
end getIdleTimeFromLastActivity

(*
	Detect if there is current user activity.

	This is a placeholder that would need to be enhanced with
	actual activity detection methods (requires additional tools
	or scripting additions on Mac OS 9).

	Returns: true if activity detected, false otherwise
*)
on detectCurrentActivity()
	-- Placeholder implementation
	-- On Mac OS 9, this would require:
	-- - Checking event queue
	-- - Using GetKeys() trap
	-- - Using Carbon Event Manager
	-- - Or using a scripting addition

	-- For now, always return false (no activity detected)
	return false
end detectCurrentActivity

-- =======================
-- UTILITY FUNCTIONS
-- =======================

(*
	Get the path to the HTTP helper library.

	Returns: Path to http-helper.applescript as string
*)
on getLibraryPath()
	set scriptPath to path to me as string

	set oldDelims to AppleScript's text item delimiters
	set AppleScript's text item delimiters to ":"
	set pathComponents to text items of scriptPath
	set AppleScript's text item delimiters to oldDelims

	set libFolder to ""
	repeat with i from 1 to (count of pathComponents) - 1
		set libFolder to libFolder & (item i of pathComponents) & ":"
	end repeat
	set libFolder to libFolder & "lib:http-helper.applescript"

	return libFolder
end getLibraryPath

(*
	Get human-readable name of detection method.

	Returns: Detection method description as string
*)
on getDetectionMethodName()
	if useScreenSaver then
		return "Screen Saver activation"
	else if useLastActivityTime then
		return "Last activity time"
	else
		return "Unknown"
	end if
end getDetectionMethodName

(*
	Log a message to console.

	Parameters:
		msg - Message to log
*)
on logMessage(msg)
	log msg
end logMessage

-- =======================
-- USAGE NOTES
-- =======================

(*
USAGE INSTRUCTIONS:

1. CONFIGURATION:
   - Edit properties in CONFIGURATION section
   - Set idleThresholdSeconds (default: 900 = 15 minutes)
   - Set checkInterval (default: 5 seconds)
   - Choose detection method (useScreenSaver recommended)

2. IDLE DETECTION METHODS:

   A. Screen Saver Method (RECOMMENDED):
      - Set useScreenSaver to true
      - Configure Mac OS 9 screen saver to activate after desired idle time
      - This script will detect screen saver and trigger restart
      - Advantages: Simple, reliable, user-visible indication
      - Limitations: Requires screen saver to be configured

   B. Last Activity Method (FALLBACK):
      - Set useLastActivityTime to true
      - Less accurate, requires additional tools for proper detection
      - Placeholder implementation provided
      - Needs enhancement with actual activity detection

3. COMPILING:
   - Open in Script Editor
   - File → Save As... → Application
   - Check "Stay Open"
   - Check "Never Show Startup Screen"

4. STARTUP:
   - Create alias and place in System Folder:Startup Items
   - Or double-click to start manually

5. TESTING:
   - Launch the application
   - Activate screen saver or wait for idle threshold
   - Verify VM restart is triggered
   - Check host controller logs

6. TROUBLESHOOTING:
   - If idle never detected:
     → Verify screen saver is configured and activating
     → Check System Events is available
     → Try the Last Activity method
   - If restart not triggered:
     → Verify host controller is reachable
     → Check HTTP helper is working
     → Verify API endpoint is correct

7. EXHIBITION SETUP:
   - Set idleThresholdSeconds slightly longer than screen saver activation
   - Example: Screen saver at 14 minutes, restart at 15 minutes
   - This gives visitors clear visual feedback before restart

8. MAC OS 9 LIMITATIONS:
   - Native idle time detection is limited
   - Screen saver method is most reliable
   - For better detection, consider:
     → Third-party scripting additions
     → Carbon Event Manager tools
     → Custom OSAX (scripting addition)
*)
