(*
Process Watchdog for Exhibition VM Controller
Mac OS 9 Guest Monitoring Scripts

Purpose: Monitor target application and trigger VM restart if it quits
Author: Exhibition VM Controller Project
License: MIT

This script monitors a target application (e.g., web browser, artwork application)
and ensures it remains running and properly configured. Features:

- Launches application on startup
- Monitors application process status
- Keeps application window focused and maximized
- Closes unauthorized windows
- Triggers VM restart if application quits unexpectedly

This is useful for exhibition environments where a specific application must
always be running and properly displayed.

IMPORTANT: This must be saved as a "Stay-Open Application" in Script Editor.

Setup:
1. Open this script in Script Editor
2. Modify configuration properties (especially targetApplication)
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

-- Application monitoring
property targetApplication : "Internet Explorer" -- Name of application to monitor
property launchOnStartup : true -- Launch application when this script starts
property launchDelay : 5 -- Seconds to wait after launching before monitoring

-- Application management
property keepFocused : true -- Keep application frontmost
property keepMaximized : false -- Attempt to maximize window (limited on Mac OS 9)
property setWindowBounds : true -- Set specific window bounds
property windowBounds : {0, 40, 1024, 768} -- {left, top, right, bottom} in pixels

-- Process monitoring
property monitorInterval : 1 -- Seconds between checks (default: 1)
property restartEndpoint : "/api/v1/vm/restart" -- API endpoint to trigger restart

-- Window management
property closeUnauthorizedWindows : false -- Close windows from other applications
property allowedApplications : {"Finder", "Script Editor"} -- Apps allowed to have windows

-- Logging
property verboseLogging : false -- Enable detailed logging (can be noisy)

-- Runtime state
property httpLib : missing value -- HTTP helper library reference
property monitoringStarted : false -- Whether monitoring has started
property checkCount : 0 -- Number of checks performed
property applicationLaunched : false -- Whether we launched the application

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
	logMessage("Process Watchdog for Mac OS 9")
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

	-- Display configuration
	logMessage("Configuration:")
	logMessage("  Host: " & hostIP & ":" & apiPort)
	logMessage("  Target application: " & targetApplication)
	logMessage("  Launch on startup: " & launchOnStartup)
	logMessage("  Monitor interval: " & monitorInterval & " seconds")
	logMessage("  Keep focused: " & keepFocused)
	logMessage("  Set window bounds: " & setWindowBounds)
	logMessage("")

	-- Launch application if configured
	if launchOnStartup then
		logMessage("Launching application: " & targetApplication)
		launchApplication()

		logMessage("Waiting " & launchDelay & " seconds for application to start...")
		delay launchDelay
		logMessage("")
	end if

	logMessage("Process watchdog is now running")
	logMessage("Press Cmd+Q to quit")
	logMessage("========================================")
	logMessage("")

	set monitoringStarted to true

	-- Idle handler will take over from here
end run

(*
	Idle handler - called repeatedly while script is running.

	Returns: Number of seconds to wait before calling idle again
*)
on idle
	if not monitoringStarted then
		return monitorInterval
	end if

	try
		set checkCount to checkCount + 1

		-- Check if application is running
		if not isApplicationRunning() then
			logMessage("")
			logMessage("========================================")
			logMessage("CRITICAL: Application not running")
			logMessage("Application: " & targetApplication)
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
			logMessage("Exiting process watchdog")
			error "Application closed - VM restarting"
		end if

		-- Manage window state
		if keepFocused or keepMaximized or setWindowBounds then
			manageWindowState()
		end if

		-- Close unauthorized windows (if enabled)
		if closeUnauthorizedWindows then
			closeUnauthorizedWindowsList()
		end if

		if verboseLogging and (checkCount mod 60 = 0) then
			logMessage("Check #" & checkCount & ": Application is running")
		end if

	on error errMsg
		logMessage("ERROR in process watchdog: " & errMsg)
	end try

	-- Return interval before next idle call
	return monitorInterval
end idle

(*
	Quit handler - called when script is quitting.
*)
on quit
	logMessage("")
	logMessage("========================================")
	logMessage("Process watchdog shutting down")
	logMessage("Total checks performed: " & checkCount)
	logMessage("========================================")
	continue quit
end quit

-- =======================
-- APPLICATION MANAGEMENT
-- =======================

(*
	Launch the target application.
*)
on launchApplication()
	try
		tell application targetApplication
			activate
		end tell

		set applicationLaunched to true
		logMessage("Application launched: " & targetApplication)

	on error errMsg
		logMessage("ERROR launching application: " & errMsg)
		logMessage("Application may need to be launched manually")
	end try
end launchApplication

(*
	Check if the target application is running.

	Returns: true if application is running, false otherwise
*)
on isApplicationRunning()
	try
		tell application "System Events"
			return (exists process targetApplication)
		end tell
	on error
		-- Fallback: Use Finder
		try
			tell application "Finder"
				return (exists application process targetApplication)
			end tell
		on error
			-- Can't determine, assume not running
			return false
		end try
	end try
end isApplicationRunning

-- =======================
-- WINDOW MANAGEMENT
-- =======================

(*
	Manage the window state of the target application.
	Keeps it focused and optionally sets window bounds.
*)
on manageWindowState()
	try
		tell application "System Events"
			if exists process targetApplication then
				tell process targetApplication
					-- Bring to front
					if keepFocused then
						set frontmost to true
					end if

					-- Manage window bounds
					if (setWindowBounds or keepMaximized) and (exists window 1) then
						manageWindow(window 1)
					end if
				end tell
			end if
		end tell
	on error errMsg
		if verboseLogging then
			logMessage("Note: Could not manage window state: " & errMsg)
		end if
	end try
end manageWindowState

(*
	Manage a specific window's bounds and state.

	Parameters:
		theWindow - Window reference from System Events
*)
on manageWindow(theWindow)
	try
		tell application "System Events"
			tell theWindow
				if setWindowBounds then
					-- Set specific bounds
					set position to {item 1 of windowBounds, item 2 of windowBounds}
					set size to {(item 3 of windowBounds) - (item 1 of windowBounds), (item 4 of windowBounds) - (item 2 of windowBounds)}
				end if

				-- Note: Mac OS 9 doesn't have native "maximized" concept
				-- The bounds setting above is the closest equivalent
			end tell
		end tell
	on error
		-- Silently ignore - some windows don't support bounds changes
	end try
end manageWindow

(*
	Close windows from unauthorized applications.
	This helps maintain a clean exhibition environment.
*)
on closeUnauthorizedWindowsList()
	try
		tell application "System Events"
			set allProcesses to every process whose visible is true

			repeat with proc in allProcesses
				set procName to name of proc

				-- Check if this process is authorized
				if procName is not targetApplication and not isInList(procName, allowedApplications) then
					try
						-- Attempt to close all windows of unauthorized application
						tell proc
							close every window
						end tell

						if verboseLogging then
							logMessage("Closed windows from unauthorized application: " & procName)
						end if
					end try
				end if
			end repeat
		end tell
	on error
		-- Silently ignore errors in window closing
	end try
end closeUnauthorizedWindowsList

(*
	Check if an item is in a list.

	Parameters:
		theItem - Item to search for
		theList - List to search in

	Returns: true if item is in list, false otherwise
*)
on isInList(theItem, theList)
	repeat with listItem in theList
		if listItem is theItem then
			return true
		end if
	end repeat
	return false
end isInList

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
   - Set targetApplication to the application name to monitor
   - Common applications: "Internet Explorer", "Netscape Navigator", "iCab"
   - Enable/disable features as needed

2. APPLICATION NAMES:
   - Use the exact application name as it appears in the Application Menu
   - NOT the filename - use the application's display name
   - Examples:
     → "Internet Explorer" not "Internet Explorer 5"
     → "Netscape Navigator" not "Netscape"

3. WINDOW BOUNDS:
   - windowBounds format: {left, top, right, bottom} in pixels
   - Example for 1024x768: {0, 40, 1024, 768}
   - Top value typically 40 to account for menu bar
   - Adjust based on your VM's display resolution

4. COMPILING:
   - Open in Script Editor
   - File → Save As... → Application
   - Check "Stay Open"
   - Check "Never Show Startup Screen"

5. STARTUP:
   - Create alias and place in System Folder:Startup Items
   - Launch AFTER target application or enable launchOnStartup

6. TESTING:
   - Launch the watchdog application
   - Verify target application is running or launched
   - Try quitting target application - VM should restart
   - Check window stays focused and sized correctly

7. TROUBLESHOOTING:
   - If application doesn't launch:
     → Verify application name is correct
     → Try launching manually first
     → Check application exists on system
   - If window management doesn't work:
     → Some applications don't support System Events control
     → Try disabling keepFocused or setWindowBounds
     → Check System Events is installed
   - If restart not triggered:
     → Verify HTTP helper is working
     → Check host controller logs

8. EXHIBITION SETUP:
   - Set launchOnStartup to true for automatic operation
   - Configure windowBounds to match display size
   - Enable keepFocused to prevent visitor from accessing other apps
   - Test full cycle: launch, run, quit app, verify restart

9. ADVANCED FEATURES:
   - closeUnauthorizedWindows: Closes windows from other apps
   - Useful for preventing visitors from accessing system
   - Add trusted apps to allowedApplications list
   - Use cautiously - may interfere with debugging

10. MAC OS 9 LIMITATIONS:
   - System Events support varies by version
   - Some apps don't support window control
   - Window "maximizing" is simulated via bounds
   - Process detection may be limited on early versions
*)
