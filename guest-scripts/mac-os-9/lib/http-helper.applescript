(*
HTTP Helper Library for Exhibition VM Controller
Mac OS 9 Guest Monitoring Scripts

Purpose: Provides HTTP communication functions for monitoring scripts
Author: Exhibition VM Controller Project
License: MIT

This library handles HTTP communication with the host controller API.
It supports multiple methods with automatic fallback:
1. URL Access Scripting (built-in to Mac OS 9)
2. curl via do shell script (if available)

Usage:
	Load this script and call its handlers from monitoring scripts.
*)

-- =======================
-- CONFIGURATION
-- =======================

property hostIP : "192.168.122.1" -- Host controller IP address
property apiPort : "8000" -- API port number
property useURLAccessScripting : true -- Use URL Access Scripting method
property useCurl : false -- Use curl if available
property httpMethodDetected : false -- Whether we've detected available methods
property tempFolder : "Temporary Items" -- Temporary folder for URL Access Scripting

-- =======================
-- INITIALIZATION
-- =======================

(*
	Initialize the HTTP helper and detect available methods.
	Call this once before using sendRequest().
	Returns: true if at least one HTTP method is available, false otherwise
*)
on initialize()
	if httpMethodDetected then
		return true
	end if

	log "Initializing HTTP helper..."

	-- Try URL Access Scripting first (most compatible)
	try
		tell application "URL Access Scripting"
			-- Just checking if it exists
		end tell
		set useURLAccessScripting to true
		log "URL Access Scripting is available"
		set httpMethodDetected to true
		return true
	on error errMsg
		set useURLAccessScripting to false
		log "URL Access Scripting not available: " & errMsg
	end try

	-- Fallback: Try curl via shell
	try
		do shell script "curl --version"
		set useCurl to true
		log "curl is available via shell"
		set httpMethodDetected to true
		return true
	on error errMsg
		set useCurl to false
		log "curl not available: " & errMsg
	end try

	if not httpMethodDetected then
		log "ERROR: No HTTP method available!"
		return false
	end if

	return true
end initialize

-- =======================
-- HTTP REQUEST
-- =======================

(*
	Send an HTTP GET request to the specified API endpoint.

	Parameters:
		endpoint - API endpoint path (e.g., "/api/v1/heartbeat")

	Returns: true if successful, false otherwise
*)
on sendRequest(endpoint)
	if not httpMethodDetected then
		log "ERROR: HTTP helper not initialized. Call initialize() first."
		return false
	end if

	set apiURL to "http://" & hostIP & ":" & apiPort & endpoint

	-- Try primary method
	if useURLAccessScripting then
		return sendViaURLAccessScripting(apiURL)
	else if useCurl then
		return sendViaCurl(apiURL)
	else
		log "ERROR: No HTTP method available"
		return false
	end if
end sendRequest

-- =======================
-- METHOD 1: URL ACCESS SCRIPTING
-- =======================

(*
	Send HTTP request using URL Access Scripting (built-in to Mac OS 9).
	This method is available on all Mac OS 9 versions.

	Note: Creates a temporary file. The file is not cleaned up automatically
	as cleanup can fail and block the script. Mac OS 9 cleans temp folder on restart.
*)
on sendViaURLAccessScripting(url)
	try
		-- Generate unique temporary file name using current time
		set tempFile to getTempFilePath()

		tell application "URL Access Scripting"
			-- Download URL to temporary file
			-- Using "with progress" to avoid UI dialogs
			download url to file tempFile without progress
		end tell

		-- Optionally: Try to delete temp file, but don't fail if we can't
		try
			tell application "Finder"
				if exists file tempFile then
					delete file tempFile
				end if
			end tell
		end try

		return true

	on error errMsg
		log "URL Access Scripting request failed: " & errMsg
		return false
	end try
end sendViaURLAccessScripting

-- =======================
-- METHOD 2: CURL VIA SHELL
-- =======================

(*
	Send HTTP request using curl command via do shell script.
	This method requires MPW (Macintosh Programmer's Workshop) and curl installed.

	Note: Not all Mac OS 9 systems have MPW and curl installed.
*)
on sendViaCurl(url)
	try
		-- Use curl with:
		-- -s = silent mode (no progress bar)
		-- -m 2 = max time 2 seconds
		-- --connect-timeout 2 = connection timeout 2 seconds
		do shell script "curl -s -m 2 --connect-timeout 2 '" & url & "'"
		return true

	on error errMsg
		log "curl request failed: " & errMsg
		return false
	end try
end sendViaCurl

-- =======================
-- NETWORK CONNECTIVITY
-- =======================

(*
	Wait for network connectivity to the host.
	Blocks until the host is reachable.

	This should be called once on script startup to ensure network is ready
	before beginning monitoring operations.
*)
on waitForNetwork()
	log "Waiting for network connectivity to " & hostIP & "..."

	repeat
		if isHostReachable() then
			log "Network is ready - host is reachable"
			exit repeat
		else
			log "Host not reachable, retrying in 1 second..."
			delay 1
		end if
	end repeat
end waitForNetwork

(*
	Check if the host is reachable.

	Returns: true if host responds to ping, false otherwise
*)
on isHostReachable()
	try
		-- Try to ping the host
		-- Using minimal timeout for quick failure
		do shell script "ping -c 1 -t 1 " & hostIP
		return true
	on error
		-- Try alternative: attempt to connect to API port
		try
			-- Use telnet with short timeout to test port
			do shell script "echo | telnet " & hostIP & " " & apiPort & " 2>&1 | grep -q Connected"
			return true
		on error
			return false
		end try
	end try
end isHostReachable

-- =======================
-- HELPER FUNCTIONS
-- =======================

(*
	Generate a unique temporary file path.
	Uses current time (ticks since system startup) to create unique names.

	Returns: File path as string in Mac OS 9 format (colon-separated)
*)
on getTempFilePath()
	-- Get boot volume name
	tell application "Finder"
		set bootDisk to name of startup disk
	end tell

	-- Generate unique filename using ticks (milliseconds since system boot)
	set uniqueName to "vmctl_" & (current date) & ".tmp"

	-- Build path: "BootDisk:Temporary Items:vmctl_timestamp.tmp"
	set tempPath to bootDisk & ":" & tempFolder & ":" & uniqueName

	return tempPath
end getTempFilePath

(*
	Get the current host IP address.

	Returns: Host IP address as string
*)
on getHostIP()
	return hostIP
end getHostIP

(*
	Set a new host IP address.

	Parameters:
		newIP - New IP address as string
*)
on setHostIP(newIP)
	set hostIP to newIP
	log "Host IP changed to: " & hostIP
end setHostIP

(*
	Get the current API port.

	Returns: API port as string
*)
on getAPIPort()
	return apiPort
end getAPIPort

(*
	Set a new API port.

	Parameters:
		newPort - New port number as string
*)
on setAPIPort(newPort)
	set apiPort to newPort
	log "API port changed to: " & apiPort
end setAPIPort
