(*
Heartbeat Monitor for Exhibition VM Controller
Mac OS 9 Guest Monitoring Scripts

Purpose: Send periodic heartbeat signals to prove VM is alive and functioning
Author: Exhibition VM Controller Project
License: MIT

This script sends heartbeat signals every second to the host controller API.
If heartbeats stop, the host controller detects timeout and restarts the VM.

Additionally, this script acts as a supervisor, checking that other monitoring
scripts are running. If a critical script is missing, this script exits,
causing the heartbeat to stop and triggering VM restart.

IMPORTANT: This must be saved as a "Stay-Open Application" in Script Editor.

Setup:
1. Open this script in Script Editor
2. Modify configuration properties below (hostIP, etc.)
3. Save As... → File Format: Application
4. Check "Stay Open" option
5. Check "Never Show Startup Screen" option
6. Place in System Folder:Startup Items for automatic launch

Configuration:
- Modify the properties in the CONFIGURATION section below
- Set hostIP to your host controller's IP address
- Enable/disable process checking as needed
*)

-- =======================
-- CONFIGURATION
-- =======================

-- Network settings
property hostIP : "192.168.122.1" -- Host controller IP address
property apiPort : "8000" -- API port number

-- Heartbeat settings
property heartbeatInterval : 1 -- Seconds between heartbeats (recommend: 1)
property heartbeatEndpoint : "/api/v1/heartbeat" -- API endpoint for heartbeat

-- Process supervision
property checkProcesses : false -- Enable checking if other monitors are running
property processNames : {"idle-monitor", "process-watchdog"} -- Processes to check

-- Logging
property verboseLogging : true -- Enable detailed logging
property logFilePath : "" -- Path to log file (empty = console only)

-- Runtime state
property httpLib : missing value -- HTTP helper library reference
property heartbeatCount : 0 -- Total heartbeats sent
property failedHeartbeats : 0 -- Consecutive failed heartbeats

-- =======================
-- MAIN ENTRY POINT
-- =======================

(*
	Main initialization - called when script app launches.
	This runs once at startup.
*)
on run
	-- Display startup banner
	logMessage("========================================")
	logMessage("Exhibition VM Controller")
	logMessage("Heartbeat Monitor for Mac OS 9")
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
		logMessage("Make sure http-helper.applescript is in the lib folder")
		error "Cannot load HTTP helper library"
	end try

	-- Configure HTTP helper with our settings
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
		logMessage("Cannot send heartbeats without HTTP support")
		error "No HTTP method available"
	end if

	logMessage("HTTP helper initialized successfully")
	logMessage("")

	-- Display configuration
	logMessage("Configuration:")
	logMessage("  Host: " & hostIP & ":" & apiPort)
	logMessage("  Heartbeat interval: " & heartbeatInterval & " seconds")
	logMessage("  Process checking: " & checkProcesses)
	if checkProcesses then
		logMessage("  Monitored processes: " & processNamesToString())
	end if
	logMessage("")

	logMessage("Heartbeat monitor is now running")
	logMessage("Press Cmd+Q to quit")
	logMessage("========================================")
	logMessage("")

	-- Start heartbeat loop (handled by idle handler)
end run

(*
	Idle handler - called repeatedly while script is running.
	This is where the main monitoring loop happens.

	Returns: Number of seconds to wait before calling idle again
*)
on idle
	try
		-- Send heartbeat
		sendHeartbeat()

		-- Check if other monitoring scripts are running (if enabled)
		if checkProcesses then
			checkCriticalProcesses()
		end if

	on error errMsg
		logMessage("ERROR in heartbeat loop: " & errMsg)
		set failedHeartbeats to failedHeartbeats + 1

		-- If too many consecutive failures, something is seriously wrong
		if failedHeartbeats > 10 then
			logMessage("FATAL: Too many consecutive heartbeat failures")
			logMessage("Exiting to trigger VM restart...")
			error "Too many heartbeat failures"
		end if
	end try

	-- Return interval before next idle call
	return heartbeatInterval
end idle

(*
	Quit handler - called when script is quitting.
	Logs shutdown message.
*)
on quit
	logMessage("")
	logMessage("========================================")
	logMessage("Heartbeat monitor shutting down")
	logMessage("Total heartbeats sent: " & heartbeatCount)
	logMessage("========================================")
	continue quit
end quit

-- =======================
-- HEARTBEAT FUNCTIONS
-- =======================

(*
	Send a heartbeat signal to the host controller.
*)
on sendHeartbeat()
	set success to httpLib's sendRequest(heartbeatEndpoint)

	if success then
		set heartbeatCount to heartbeatCount + 1
		set failedHeartbeats to 0 -- Reset failure counter

		if verboseLogging then
			logMessage("Heartbeat #" & heartbeatCount & " sent successfully")
		end if
	else
		set failedHeartbeats to failedHeartbeats + 1
		logMessage("WARNING: Heartbeat failed (consecutive failures: " & failedHeartbeats & ")")
	end if
end sendHeartbeat

-- =======================
-- PROCESS SUPERVISION
-- =======================

(*
	Check if all critical monitoring processes are running.
	If any critical process is missing, exit immediately to trigger VM restart.
*)
on checkCriticalProcesses()
	repeat with processName in processNames
		set pName to processName as string

		if not isProcessRunning(pName) then
			logMessage("")
			logMessage("========================================")
			logMessage("CRITICAL: Process not running: " & pName)
			logMessage("Exiting to trigger VM restart...")
			logMessage("========================================")

			-- Exit immediately - this stops heartbeats and triggers timeout
			error "Critical process missing: " & pName
		end if
	end repeat
end checkCriticalProcesses

(*
	Check if a process is running.

	Parameters:
		processName - Name of the process to check

	Returns: true if process is running, false otherwise
*)
on isProcessRunning(processName)
	try
		tell application "System Events"
			return (exists process processName)
		end tell
	on error
		-- If System Events not available, use Finder as fallback
		try
			tell application "Finder"
				return (exists application process processName)
			end tell
		on error
			-- Can't determine, assume it's not running
			return false
		end try
	end try
end isProcessRunning

-- =======================
-- UTILITY FUNCTIONS
-- =======================

(*
	Get the path to the HTTP helper library.

	Returns: Path to http-helper.applescript as string
*)
on getLibraryPath()
	-- Get the path to this script
	set scriptPath to path to me as string

	-- Parse out the folder containing this script
	set oldDelims to AppleScript's text item delimiters
	set AppleScript's text item delimiters to ":"
	set pathComponents to text items of scriptPath
	set AppleScript's text item delimiters to oldDelims

	-- Build path to lib folder
	-- If script is in "Monitoring" folder, lib is in "Monitoring:lib"
	set libFolder to ""
	repeat with i from 1 to (count of pathComponents) - 1
		set libFolder to libFolder & (item i of pathComponents) & ":"
	end repeat
	set libFolder to libFolder & "lib:http-helper.applescript"

	return libFolder
end getLibraryPath

(*
	Convert process names list to string for logging.

	Returns: Comma-separated string of process names
*)
on processNamesToString()
	set resultString to ""
	repeat with processName in processNames
		if resultString is not "" then
			set resultString to resultString & ", "
		end if
		set resultString to resultString & (processName as string)
	end repeat
	return resultString
end processNamesToString

(*
	Log a message to console and/or file.

	Parameters:
		msg - Message to log
*)
on logMessage(msg)
	-- Always log to console (visible during development)
	log msg

	-- Optionally log to file (if path specified)
	if logFilePath is not "" then
		try
			set logFile to open for access file logFilePath with write permission
			write (msg & return) to logFile starting at eof
			close access logFile
		on error
			-- Silently ignore file logging errors
			try
				close access file logFilePath
			end try
		end try
	end if
end logMessage

-- =======================
-- USAGE NOTES
-- =======================

(*
USAGE INSTRUCTIONS:

1. CONFIGURATION:
   - Edit the properties in the CONFIGURATION section above
   - Set hostIP to your host controller's IP address (usually 192.168.122.1)
   - Adjust heartbeatInterval if needed (1 second is recommended)
   - Enable checkProcesses if you want this script to supervise other monitors

2. COMPILING:
   - Open this script in Script Editor
   - Choose File → Save As...
   - File Format: Application
   - Options: Check "Stay Open"
   - Options: Check "Never Show Startup Screen"
   - Save to: Macintosh HD:Monitoring:heartbeat

3. STARTUP:
   - To launch automatically on boot, create an alias:
     - Select the heartbeat application
     - File → Make Alias
     - Move alias to: System Folder:Startup Items:
   - Or double-click the application to start manually

4. TESTING:
   - Launch the application
   - Open Script Editor's Event Log to see messages
   - Verify heartbeats are being sent (check host controller logs)
   - To stop: Press Cmd+Q or choose Quit from application menu

5. TROUBLESHOOTING:
   - If "Cannot load HTTP helper library" error:
     → Make sure http-helper.applescript is in the lib folder
     → lib folder should be in same parent folder as this script
   - If "No HTTP method available" error:
     → URL Access Scripting may not be installed
     → Try installing MPW and curl
   - If "Network not ready" loops forever:
     → Check VM network configuration
     → Verify host IP address is correct
     → Try pinging host from another application

6. PROCESS SUPERVISION:
   - Enable checkProcesses to have this script verify other monitors are running
   - Add process names to processNames list (use application names, not file names)
   - If a critical process is missing, this script exits → heartbeat stops → VM restarts
   - Example: processNames = {"idle-monitor", "process-watchdog"}

7. LOGGING:
   - Messages are logged to console (visible in Script Editor Event Log)
   - Optionally set logFilePath to log to a file
   - Example: logFilePath = "Macintosh HD:Monitoring:Logs:heartbeat.log"
*)
