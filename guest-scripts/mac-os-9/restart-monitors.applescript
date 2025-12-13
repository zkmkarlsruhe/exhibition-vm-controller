(*
Restart Monitors Utility for Exhibition VM Controller
Mac OS 9 Guest Monitoring Scripts

Purpose: Restart all monitoring scripts for recovery and testing
Author: Exhibition VM Controller Project
License: MIT

This utility script helps with:
- Manual recovery when monitors malfunction
- Testing and development
- Restarting monitors after configuration changes

It will:
1. Quit all running monitoring scripts
2. Wait for clean shutdown
3. Relaunch all monitors

IMPORTANT: This is a regular script (NOT stay-open). It runs once and quits.

Usage:
- Double-click to run
- Or run from Script Editor for debugging
*)

-- =======================
-- CONFIGURATION
-- =======================

-- Monitoring scripts to restart (application names)
property monitorScripts : {"heartbeat", "idle-monitor", "process-watchdog"}

-- Timing
property shutdownDelay : 5 -- Seconds to wait after quitting before relaunching
property launchDelay : 2 -- Seconds to wait between launching each script

-- Paths (adjust these to match your installation)
property monitoringFolder : "Macintosh HD:Monitoring:" -- Folder containing monitor apps

-- =======================
-- MAIN SCRIPT
-- =======================

-- Display banner
logMessage("========================================")
logMessage("Exhibition VM Controller")
logMessage("Restart Monitors Utility")
logMessage("========================================")
logMessage("")

-- Step 1: Quit all monitoring scripts
logMessage("Step 1: Stopping all monitoring scripts...")
logMessage("")

repeat with scriptName in monitorScripts
	set appName to scriptName as string
	logMessage("Stopping: " & appName)

	try
		tell application appName
			if it is running then
				quit
				logMessage("  → Quit command sent")
			else
				logMessage("  → Not running")
			end if
		end tell
	on error errMsg
		logMessage("  → Error: " & errMsg)
	end try
end repeat

logMessage("")
logMessage("Waiting " & shutdownDelay & " seconds for clean shutdown...")
delay shutdownDelay
logMessage("")

-- Step 2: Verify all scripts have stopped
logMessage("Step 2: Verifying scripts have stopped...")
logMessage("")

set allStopped to true

repeat with scriptName in monitorScripts
	set appName to scriptName as string

	if isProcessRunning(appName) then
		logMessage("WARNING: " & appName & " is still running")
		set allStopped to false

		-- Try to force quit
		try
			tell application "System Events"
				tell process appName
					set visible to false
				end tell
			end tell
			logMessage("  → Attempted force quit")
		end try
	else
		logMessage("✓ " & appName & " stopped successfully")
	end if
end repeat

logMessage("")

if not allStopped then
	logMessage("WARNING: Some scripts did not stop cleanly")
	logMessage("You may need to force quit them manually")
	logMessage("")
end if

-- Step 3: Relaunch all monitoring scripts
logMessage("Step 3: Relaunching monitoring scripts...")
logMessage("")

repeat with scriptName in monitorScripts
	set appName to scriptName as string
	set appPath to monitoringFolder & appName

	logMessage("Launching: " & appName)

	try
		tell application "Finder"
			if exists file appPath then
				open file appPath
				logMessage("  → Launched successfully")
			else
				logMessage("  → ERROR: Application not found at: " & appPath)
			end if
		end tell
	on error errMsg
		logMessage("  → ERROR launching: " & errMsg)
	end try

	-- Wait between launches
	if launchDelay > 0 then
		delay launchDelay
	end if
end repeat

logMessage("")
logMessage("========================================")
logMessage("Restart complete!")
logMessage("")
logMessage("All monitoring scripts should now be running.")
logMessage("Check their status:")
logMessage("  - Open Process Manager (System menu)")
logMessage("  - Look for: heartbeat, idle-monitor, process-watchdog")
logMessage("========================================")

-- =======================
-- HELPER FUNCTIONS
-- =======================

(*
	Check if a process is running.

	Parameters:
		processName - Name of the process to check

	Returns: true if running, false otherwise
*)
on isProcessRunning(processName)
	try
		tell application "System Events"
			return (exists process processName)
		end tell
	on error
		-- Fallback: Use Finder
		try
			tell application "Finder"
				return (exists application process processName)
			end tell
		on error
			return false
		end try
	end try
end isProcessRunning

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
   - Set monitoringFolder to the folder containing your monitor applications
   - Adjust monitorScripts list if you have different script names
   - Example: monitoringFolder = "Macintosh HD:Monitoring:"

2. SAVING THE SCRIPT:
   - This is a regular script, NOT a stay-open application
   - Save As... → File Format: Application
   - Do NOT check "Stay Open"
   - Save to: Macintosh HD:Monitoring:restart-monitors

3. RUNNING:
   - Double-click the application to run
   - Or open in Script Editor and click Run
   - Script will run once and quit when done

4. WHEN TO USE:
   - After changing configuration in monitor scripts
   - When monitors appear to be malfunctioning
   - During testing and development
   - As part of manual recovery procedure

5. WHAT IT DOES:
   - Sends quit command to all monitoring scripts
   - Waits for them to shut down cleanly
   - Relaunches each script in order
   - Reports status at each step

6. TROUBLESHOOTING:
   - If scripts won't quit:
     → Use Force Quit from Special menu
     → Or use Process Manager to force quit
   - If scripts won't launch:
     → Check monitoringFolder path is correct
     → Verify script applications exist
     → Check permissions
   - If "Application not found" errors:
     → Verify script names match compiled application names
     → Check for spaces or special characters in names

7. ALTERNATIVE: MANUAL RESTART
   If this script doesn't work, restart manually:
   a. Open Process Manager (System menu)
   b. Select each monitor script and click "Quit"
   c. Wait a few seconds
   d. Navigate to Monitoring folder
   e. Double-click each monitor to relaunch

8. AUTOMATIC RESTART:
   - This script does NOT auto-restart on failure
   - For automatic operation, use the individual monitor scripts
   - This is a manual utility for recovery and testing

9. EXHIBITION USE:
   - Keep this utility available for staff
   - Place alias on desktop for easy access
   - Document recovery procedure for non-technical staff
   - Include this in exhibition maintenance documentation

10. DEVELOPMENT USE:
    - Use during development to quickly restart after code changes
    - Run from Script Editor to see detailed log output
    - Adjust delays if needed for your testing environment
*)
