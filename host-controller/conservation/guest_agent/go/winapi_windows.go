// Win32 debug tools for the legacy agent — introspection + driving a black-box
// app from inside the guest. The _windows.go suffix restricts this file to
// GOOS=windows (honored by every Go version incl. 1.10), so the cross-platform
// code in legacy_agent.go still builds on Linux. All tools here register
// themselves into the global `tools` map via init(); no third-party deps.

package main

import (
	"bytes"
	"context"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"os/exec"
	"strings"
	"syscall"
	"unsafe"
)

var (
	user32   = syscall.NewLazyDLL("user32.dll")
	gdi32    = syscall.NewLazyDLL("gdi32.dll")
	kernel32 = syscall.NewLazyDLL("kernel32.dll")
	advapi32 = syscall.NewLazyDLL("advapi32.dll")

	pRegCreateKeyExW = advapi32.NewProc("RegCreateKeyExW")
	pRegSetValueExW  = advapi32.NewProc("RegSetValueExW")
	pRegCloseKey     = advapi32.NewProc("RegCloseKey")
	pRegOpenKeyExW   = advapi32.NewProc("RegOpenKeyExW")
	pRegQueryValueExW = advapi32.NewProc("RegQueryValueExW")
	pRegDeleteValueW = advapi32.NewProc("RegDeleteValueW")
	pRegDeleteKeyW   = advapi32.NewProc("RegDeleteKeyW")

	pEnumWindows              = user32.NewProc("EnumWindows")
	pEnumChildWindows         = user32.NewProc("EnumChildWindows")
	pIsWindowVisible          = user32.NewProc("IsWindowVisible")
	pGetWindowTextW           = user32.NewProc("GetWindowTextW")
	pGetWindowTextLengthW     = user32.NewProc("GetWindowTextLengthW")
	pGetClassNameW            = user32.NewProc("GetClassNameW")
	pGetWindowThreadProcessId = user32.NewProc("GetWindowThreadProcessId")
	pGetWindowRect            = user32.NewProc("GetWindowRect")
	pFindWindowW              = user32.NewProc("FindWindowW")
	pShowWindow               = user32.NewProc("ShowWindow")
	pSetForegroundWindow      = user32.NewProc("SetForegroundWindow")
	pGetForegroundWindow      = user32.NewProc("GetForegroundWindow")
	pPostMessageW             = user32.NewProc("PostMessageW")
	pSetCursorPos             = user32.NewProc("SetCursorPos")
	pMouseEvent               = user32.NewProc("mouse_event")
	pKeybdEvent               = user32.NewProc("keybd_event")
	pVkKeyScanW               = user32.NewProc("VkKeyScanW")
	pGetSystemMetrics         = user32.NewProc("GetSystemMetrics")
	pGetDesktopWindow         = user32.NewProc("GetDesktopWindow")
	pGetWindowDC              = user32.NewProc("GetWindowDC")
	pReleaseDC                = user32.NewProc("ReleaseDC")

	pCreateCompatibleDC     = gdi32.NewProc("CreateCompatibleDC")
	pCreateCompatibleBitmap = gdi32.NewProc("CreateCompatibleBitmap")
	pSelectObject           = gdi32.NewProc("SelectObject")
	pBitBlt                 = gdi32.NewProc("BitBlt")
	pGetDIBits              = gdi32.NewProc("GetDIBits")
	pGetPixel               = gdi32.NewProc("GetPixel")
	pDeleteObject           = gdi32.NewProc("DeleteObject")
	pDeleteDC               = gdi32.NewProc("DeleteDC")

	pCreateToolhelp32Snapshot = kernel32.NewProc("CreateToolhelp32Snapshot")
	pModule32FirstW           = kernel32.NewProc("Module32FirstW")
	pModule32NextW            = kernel32.NewProc("Module32NextW")
	pCloseHandle              = kernel32.NewProc("CloseHandle")
	pOpenProcess              = kernel32.NewProc("OpenProcess")
	pTerminateProcess         = kernel32.NewProc("TerminateProcess")
)

func init() {
	// Win32-only tools register themselves so the cross-platform code never
	// references these handlers (keeps the Linux build clean).
	tools["screenshot"] = toolDef{
		desc:    "Capture the whole desktop as a base64 BMP (GDI). Run in the interactive session.",
		schema:  objSchema(nil),
		handler: toolScreenshot,
	}
	tools["list_windows"] = toolDef{
		desc:    "List visible top-level windows (title, hwnd, pid).",
		schema:  objSchema(nil),
		handler: toolListWindows,
	}
	tools["find_window"] = toolDef{
		desc: "Find a top-level window by exact title and/or class; returns its hwnd (0 if none).",
		schema: objSchema(map[string]interface{}{
			"title": map[string]interface{}{"type": "string"},
			"class": map[string]interface{}{"type": "string"},
		}),
		handler: toolFindWindow,
	}
	tools["window_info"] = toolDef{
		desc: "Details of a window: class, title, pid, rect, visible.",
		schema: objSchema(map[string]interface{}{
			"hwnd": map[string]interface{}{"type": "number"}}, "hwnd"),
		handler: toolWindowInfo,
	}
	tools["window_controls"] = toolDef{
		desc: "Enumerate child controls of a window (the no-UIA control tree) — class, text, rect, hwnd.",
		schema: objSchema(map[string]interface{}{
			"hwnd": map[string]interface{}{"type": "number"}}, "hwnd"),
		handler: toolWindowControls,
	}
	tools["show_window"] = toolDef{
		desc: "Change a window's state: hide|show|minimize|maximize|restore|foreground|close.",
		schema: objSchema(map[string]interface{}{
			"hwnd": map[string]interface{}{"type": "number"},
			"mode": map[string]interface{}{"type": "string"}}, "hwnd", "mode"),
		handler: toolShowWindow,
	}
	tools["click"] = toolDef{
		desc: "Move the mouse to x,y and click (button left|right|middle, optional double).",
		schema: objSchema(map[string]interface{}{
			"x":      map[string]interface{}{"type": "number"},
			"y":      map[string]interface{}{"type": "number"},
			"button": map[string]interface{}{"type": "string"},
			"double": map[string]interface{}{"type": "boolean"}}, "x", "y"),
		handler: toolClick,
	}
	tools["move_mouse"] = toolDef{
		desc: "Move the mouse cursor to x,y.",
		schema: objSchema(map[string]interface{}{
			"x": map[string]interface{}{"type": "number"},
			"y": map[string]interface{}{"type": "number"}}, "x", "y"),
		handler: toolMoveMouse,
	}
	tools["send_keys"] = toolDef{
		desc: "Type text into a window (default: the foreground window) via WM_CHAR.",
		schema: objSchema(map[string]interface{}{
			"text": map[string]interface{}{"type": "string"},
			"hwnd": map[string]interface{}{"type": "number"}}, "text"),
		handler: toolSendKeys,
	}
	tools["send_key"] = toolDef{
		desc: "Press a virtual-key code (e.g. 13=Enter, 9=Tab, 27=Esc, 0x70=F1) — full keystroke.",
		schema: objSchema(map[string]interface{}{
			"vk": map[string]interface{}{"type": "number"}}, "vk"),
		handler: toolSendKey,
	}
	tools["pixel"] = toolDef{
		desc: "Read the screen pixel colour at x,y (for state detection / pixel triggers).",
		schema: objSchema(map[string]interface{}{
			"x": map[string]interface{}{"type": "number"},
			"y": map[string]interface{}{"type": "number"}}, "x", "y"),
		handler: toolPixel,
	}
	tools["process_modules"] = toolDef{
		desc: "List the modules (DLLs) a process has loaded — reveals codecs/plugins/engines.",
		schema: objSchema(map[string]interface{}{
			"pid": map[string]interface{}{"type": "number"}}, "pid"),
		handler: toolProcessModules,
	}
	tools["kill_process"] = toolDef{
		desc: "Forcibly terminate a process by pid.",
		schema: objSchema(map[string]interface{}{
			"pid": map[string]interface{}{"type": "number"}}, "pid"),
		handler: toolKillProcess,
	}
	tools["reg_set"] = toolDef{
		desc: "Set a registry value via the native API (works even if regedit/reg.exe are policy-disabled).",
		schema: objSchema(map[string]interface{}{
			"hive":  map[string]interface{}{"type": "string", "description": "HKCU|HKLM|HKCR|HKU"},
			"key":   map[string]interface{}{"type": "string"},
			"value": map[string]interface{}{"type": "string", "description": "value name ('' = default)"},
			"type":  map[string]interface{}{"type": "string", "description": "sz|dword|expand_sz"},
			"data":  map[string]interface{}{"description": "string or number"},
		}, "hive", "key"),
		handler: toolRegSet,
	}
	tools["reg_get"] = toolDef{
		desc: "Read a registry value via the native API. Returns {exists, type, data}.",
		schema: objSchema(map[string]interface{}{
			"hive":  map[string]interface{}{"type": "string"},
			"key":   map[string]interface{}{"type": "string"},
			"value": map[string]interface{}{"type": "string"},
		}, "hive", "key"),
		handler: toolRegGet,
	}
	tools["reg_delete"] = toolDef{
		desc: "Delete a registry value (if 'value' given) or key via the native API.",
		schema: objSchema(map[string]interface{}{
			"hive":  map[string]interface{}{"type": "string"},
			"key":   map[string]interface{}{"type": "string"},
			"value": map[string]interface{}{"type": "string"},
		}, "hive", "key"),
		handler: toolRegDelete,
	}
	tools["install_cert"] = toolDef{
		desc: "Install a CA certificate into the Windows trust store via crypt32 (no certutil needed) " +
			"— so the artwork trusts a MITM/proxy CA. Give 'pem' or 'der_base64'.",
		schema: objSchema(map[string]interface{}{
			"pem":        map[string]interface{}{"type": "string", "description": "full PEM cert text"},
			"der_base64": map[string]interface{}{"type": "string", "description": "base64 of DER cert (alt to pem)"},
			"scope":      map[string]interface{}{"type": "string", "description": "user (default) or machine"},
			"store":      map[string]interface{}{"type": "string", "description": "store name, default ROOT"},
		}),
		handler: toolInstallCert,
	}
}

func certDER(a map[string]interface{}) ([]byte, error) {
	if p := argStr(a, "pem", ""); p != "" {
		block, _ := pem.Decode([]byte(p))
		if block == nil {
			return nil, fmt.Errorf("invalid PEM")
		}
		return block.Bytes, nil
	}
	if b := argStr(a, "der_base64", ""); b != "" {
		return base64.StdEncoding.DecodeString(b)
	}
	return nil, fmt.Errorf("provide 'pem' or 'der_base64'")
}

func hiveFromString(h string) uintptr {
	switch strings.ToUpper(h) {
	case "HKLM", "HKEY_LOCAL_MACHINE":
		return 0x80000002
	case "HKCR", "HKEY_CLASSES_ROOT":
		return 0x80000000
	case "HKU", "HKEY_USERS":
		return 0x80000003
	default:
		return 0x80000001 // HKCU
	}
}

func toolRegSet(a map[string]interface{}) (interface{}, error) {
	hive := hiveFromString(argStr(a, "hive", "HKCU"))
	key := argStr(a, "key", "")
	value := argStr(a, "value", "")
	typ := argStr(a, "type", "sz")

	var rt uintptr
	var data []byte
	switch typ {
	case "dword":
		rt = 4
		data = make([]byte, 4)
		binary.LittleEndian.PutUint32(data, uint32(int64(argNum(a, "data", 0))))
	case "expand_sz", "sz":
		rt = 1
		if typ == "expand_sz" {
			rt = 2
		}
		u16, _ := syscall.UTF16FromString(argStr(a, "data", ""))
		data = make([]byte, len(u16)*2)
		for i, c := range u16 {
			binary.LittleEndian.PutUint16(data[i*2:], c)
		}
	default:
		return nil, fmt.Errorf("unsupported type %q (sz|dword|expand_sz)", typ)
	}

	var hKey uintptr
	rc, _, _ := pRegCreateKeyExW.Call(hive, uintptr(unsafe.Pointer(utf16Ptr(key))),
		0, 0, 0, 0x20006 /*KEY_WRITE*/, 0, uintptr(unsafe.Pointer(&hKey)), 0)
	if rc != 0 {
		return nil, fmt.Errorf("RegCreateKeyEx rc=%d (admin needed for HKLM?)", rc)
	}
	defer pRegCloseKey.Call(hKey)
	var dptr uintptr
	if len(data) > 0 {
		dptr = uintptr(unsafe.Pointer(&data[0]))
	}
	rc, _, _ = pRegSetValueExW.Call(hKey, uintptr(unsafe.Pointer(utf16Ptr(value))),
		0, rt, dptr, uintptr(len(data)))
	if rc != 0 {
		return nil, fmt.Errorf("RegSetValueEx rc=%d", rc)
	}
	return map[string]interface{}{"ok": true, "key": key, "value": value, "type": typ}, nil
}

func toolRegGet(a map[string]interface{}) (interface{}, error) {
	hive := hiveFromString(argStr(a, "hive", "HKCU"))
	key := argStr(a, "key", "")
	value := argStr(a, "value", "")
	var hKey uintptr
	rc, _, _ := pRegOpenKeyExW.Call(hive, uintptr(unsafe.Pointer(utf16Ptr(key))),
		0, 0x20019 /*KEY_READ*/, uintptr(unsafe.Pointer(&hKey)))
	if rc != 0 {
		return map[string]interface{}{"exists": false}, nil
	}
	defer pRegCloseKey.Call(hKey)
	var typ, size uint32
	pRegQueryValueExW.Call(hKey, uintptr(unsafe.Pointer(utf16Ptr(value))), 0,
		uintptr(unsafe.Pointer(&typ)), 0, uintptr(unsafe.Pointer(&size)))
	buf := make([]byte, size)
	var bptr uintptr
	if size > 0 {
		bptr = uintptr(unsafe.Pointer(&buf[0]))
	}
	rc, _, _ = pRegQueryValueExW.Call(hKey, uintptr(unsafe.Pointer(utf16Ptr(value))), 0,
		uintptr(unsafe.Pointer(&typ)), bptr, uintptr(unsafe.Pointer(&size)))
	if rc != 0 {
		return map[string]interface{}{"exists": false}, nil
	}
	out := map[string]interface{}{"exists": true, "type": typ}
	switch typ {
	case 4:
		out["data"] = binary.LittleEndian.Uint32(buf)
	case 1, 2:
		u16 := make([]uint16, size/2)
		for i := range u16 {
			u16[i] = binary.LittleEndian.Uint16(buf[i*2:])
		}
		out["data"] = syscall.UTF16ToString(u16)
	default:
		out["data"] = base64.StdEncoding.EncodeToString(buf)
	}
	return out, nil
}

func toolRegDelete(a map[string]interface{}) (interface{}, error) {
	hive := hiveFromString(argStr(a, "hive", "HKCU"))
	key := argStr(a, "key", "")
	if _, hasValue := a["value"]; hasValue {
		var hKey uintptr
		rc, _, _ := pRegOpenKeyExW.Call(hive, uintptr(unsafe.Pointer(utf16Ptr(key))),
			0, 0x20006, uintptr(unsafe.Pointer(&hKey)))
		if rc != 0 {
			return map[string]interface{}{"ok": true, "note": "key absent"}, nil
		}
		defer pRegCloseKey.Call(hKey)
		pRegDeleteValueW.Call(hKey, uintptr(unsafe.Pointer(utf16Ptr(argStr(a, "value", "")))))
		return map[string]interface{}{"ok": true, "deleted": "value"}, nil
	}
	pRegDeleteKeyW.Call(hive, uintptr(unsafe.Pointer(utf16Ptr(key))))
	return map[string]interface{}{"ok": true, "deleted": "key"}, nil
}

func toolInstallCert(a map[string]interface{}) (interface{}, error) {
	der, err := certDER(a)
	if err != nil {
		return nil, err
	}
	// Write the cert's serialized blob straight into the registry-backed store.
	// crypt32's CertAddEncodedCertificateToStore raises a modal "Security
	// Warning" trust prompt on root-store adds (even via the registry provider
	// on XP) — fatal for unattended use. The cert store *is* the registry, so a
	// direct write is silent. The store key is named by the cert's SHA1
	// thumbprint; the Blob is one property element (id 0x20 = encoded cert),
	// and Windows regenerates the other properties on access.
	sum := sha1.Sum(der)
	thumb := strings.ToUpper(hex.EncodeToString(sum[:]))
	store := argStr(a, "store", "ROOT")
	scope := argStr(a, "scope", "user")
	var root uintptr = 0x80000001 // HKEY_CURRENT_USER
	if scope == "machine" {
		root = 0x80000002 // HKEY_LOCAL_MACHINE (needs admin)
	}
	keyPath := `Software\Microsoft\SystemCertificates\` + store + `\Certificates\` + thumb

	blob := make([]byte, 12+len(der))
	binary.LittleEndian.PutUint32(blob[0:], 0x20) // CERT_CERT_PROP_ID (encoded cert)
	binary.LittleEndian.PutUint32(blob[4:], 1)    // flags
	binary.LittleEndian.PutUint32(blob[8:], uint32(len(der)))
	copy(blob[12:], der)

	const keyWrite = 0x20006
	const regBinary = 3
	var hKey uintptr
	rc, _, _ := pRegCreateKeyExW.Call(root, uintptr(unsafe.Pointer(utf16Ptr(keyPath))),
		0, 0, 0, keyWrite, 0, uintptr(unsafe.Pointer(&hKey)), 0)
	if rc != 0 {
		return nil, fmt.Errorf("RegCreateKeyEx failed rc=%d (admin needed for machine scope?)", rc)
	}
	defer pRegCloseKey.Call(hKey)
	rc, _, _ = pRegSetValueExW.Call(hKey, uintptr(unsafe.Pointer(utf16Ptr("Blob"))),
		0, regBinary, uintptr(unsafe.Pointer(&blob[0])), uintptr(len(blob)))
	if rc != 0 {
		return nil, fmt.Errorf("RegSetValueEx failed rc=%d", rc)
	}
	return map[string]interface{}{"installed": true, "silent": true, "store": store,
		"scope": scope, "thumbprint": thumb, "bytes": len(der)}, nil
}

// shellCommand builds `cmd /c <command>` for run_shell. It sets SysProcAttr.CmdLine
// so Go passes the command line to CreateProcess VERBATIM instead of re-quoting
// each arg (Go's escaping doesn't match cmd.exe and mangled quoted paths like
// "C:\Program Files\..." or reg keys with spaces). `/s` makes cmd strip exactly
// the outer quotes and treat the rest literally. HideWindow keeps it silent.
func shellCommand(ctx context.Context, command string) *exec.Cmd {
	c := exec.CommandContext(ctx, "cmd")
	c.SysProcAttr = &syscall.SysProcAttr{
		HideWindow: true,
		CmdLine:    `cmd /s /c "` + command + `"`,
	}
	return c
}

func utf16Ptr(s string) *uint16 {
	p, _ := syscall.UTF16PtrFromString(s)
	return p
}

func getWindowText(hwnd uintptr) string {
	n, _, _ := pGetWindowTextLengthW.Call(hwnd)
	if n == 0 {
		return ""
	}
	buf := make([]uint16, n+1)
	pGetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(&buf[0])), n+1)
	return syscall.UTF16ToString(buf)
}

func getClassName(hwnd uintptr) string {
	buf := make([]uint16, 256)
	r, _, _ := pGetClassNameW.Call(hwnd, uintptr(unsafe.Pointer(&buf[0])), 256)
	return syscall.UTF16ToString(buf[:r])
}

type rect struct{ Left, Top, Right, Bottom int32 }

func getRect(hwnd uintptr) map[string]interface{} {
	var r rect
	pGetWindowRect.Call(hwnd, uintptr(unsafe.Pointer(&r)))
	return map[string]interface{}{"left": r.Left, "top": r.Top, "right": r.Right, "bottom": r.Bottom,
		"w": r.Right - r.Left, "h": r.Bottom - r.Top}
}

func pidOf(hwnd uintptr) uint32 {
	var pid uint32
	pGetWindowThreadProcessId.Call(hwnd, uintptr(unsafe.Pointer(&pid)))
	return pid
}

func toolListWindows(a map[string]interface{}) (interface{}, error) {
	type win struct {
		HWND  uint64 `json:"hwnd"`
		Title string `json:"title"`
		Class string `json:"class"`
		PID   uint32 `json:"pid"`
	}
	windows := []win{}
	cb := syscall.NewCallback(func(hwnd, lparam uintptr) uintptr {
		if v, _, _ := pIsWindowVisible.Call(hwnd); v != 0 {
			if t := getWindowText(hwnd); t != "" {
				windows = append(windows, win{uint64(hwnd), t, getClassName(hwnd), pidOf(hwnd)})
			}
		}
		return 1
	})
	pEnumWindows.Call(cb, 0)
	return map[string]interface{}{"count": len(windows), "windows": windows}, nil
}

func toolFindWindow(a map[string]interface{}) (interface{}, error) {
	var cp, tp uintptr
	if c := argStr(a, "class", ""); c != "" {
		cp = uintptr(unsafe.Pointer(utf16Ptr(c)))
	}
	if t := argStr(a, "title", ""); t != "" {
		tp = uintptr(unsafe.Pointer(utf16Ptr(t)))
	}
	hwnd, _, _ := pFindWindowW.Call(cp, tp)
	res := map[string]interface{}{"hwnd": uint64(hwnd)}
	if hwnd != 0 {
		res["title"] = getWindowText(hwnd)
		res["class"] = getClassName(hwnd)
		res["pid"] = pidOf(hwnd)
	}
	return res, nil
}

func toolWindowInfo(a map[string]interface{}) (interface{}, error) {
	hwnd := uintptr(uint64(argNum(a, "hwnd", 0)))
	vis, _, _ := pIsWindowVisible.Call(hwnd)
	return map[string]interface{}{
		"hwnd": uint64(hwnd), "title": getWindowText(hwnd), "class": getClassName(hwnd),
		"pid": pidOf(hwnd), "visible": vis != 0, "rect": getRect(hwnd),
	}, nil
}

func toolWindowControls(a map[string]interface{}) (interface{}, error) {
	parent := uintptr(uint64(argNum(a, "hwnd", 0)))
	type ctrl struct {
		HWND  uint64                 `json:"hwnd"`
		Class string                 `json:"class"`
		Text  string                 `json:"text"`
		Rect  map[string]interface{} `json:"rect"`
	}
	controls := []ctrl{}
	cb := syscall.NewCallback(func(hwnd, lparam uintptr) uintptr {
		controls = append(controls, ctrl{uint64(hwnd), getClassName(hwnd), getWindowText(hwnd), getRect(hwnd)})
		return uintptr(boolToInt(len(controls) < 400)) // cap
	})
	pEnumChildWindows.Call(parent, cb, 0)
	return map[string]interface{}{"count": len(controls), "controls": controls}, nil
}

func toolShowWindow(a map[string]interface{}) (interface{}, error) {
	hwnd := uintptr(uint64(argNum(a, "hwnd", 0)))
	const WMCLOSE = 0x0010
	modes := map[string]uintptr{"hide": 0, "show": 5, "minimize": 6, "maximize": 3, "restore": 9}
	switch m := argStr(a, "mode", ""); m {
	case "foreground":
		pSetForegroundWindow.Call(hwnd)
	case "close":
		pPostMessageW.Call(hwnd, WMCLOSE, 0, 0)
	default:
		sw, ok := modes[m]
		if !ok {
			return nil, fmt.Errorf("unknown mode %q", m)
		}
		pShowWindow.Call(hwnd, sw)
	}
	return map[string]interface{}{"ok": true}, nil
}

func toolMoveMouse(a map[string]interface{}) (interface{}, error) {
	pSetCursorPos.Call(uintptr(int(argNum(a, "x", 0))), uintptr(int(argNum(a, "y", 0))))
	return map[string]interface{}{"ok": true}, nil
}

func toolClick(a map[string]interface{}) (interface{}, error) {
	x, y := int(argNum(a, "x", 0)), int(argNum(a, "y", 0))
	pSetCursorPos.Call(uintptr(x), uintptr(y))
	var down, up uintptr = 0x0002, 0x0004 // LEFTDOWN / LEFTUP
	switch argStr(a, "button", "left") {
	case "right":
		down, up = 0x0008, 0x0010
	case "middle":
		down, up = 0x0020, 0x0040
	}
	clicks := 1
	if argBool(a, "double") {
		clicks = 2
	}
	for i := 0; i < clicks; i++ {
		pMouseEvent.Call(down, 0, 0, 0, 0)
		pMouseEvent.Call(up, 0, 0, 0, 0)
	}
	return map[string]interface{}{"ok": true, "x": x, "y": y}, nil
}

func toolSendKeys(a map[string]interface{}) (interface{}, error) {
	hwnd := uintptr(uint64(argNum(a, "hwnd", 0)))
	if hwnd == 0 {
		hwnd, _, _ = pGetForegroundWindow.Call()
	}
	const WMCHAR = 0x0102
	for _, r := range argStr(a, "text", "") {
		pPostMessageW.Call(hwnd, WMCHAR, uintptr(r), 0)
	}
	return map[string]interface{}{"ok": true, "hwnd": uint64(hwnd)}, nil
}

func toolSendKey(a map[string]interface{}) (interface{}, error) {
	vk := uintptr(int(argNum(a, "vk", 0)))
	const KEYUP = 0x0002
	pKeybdEvent.Call(vk, 0, 0, 0)     // down
	pKeybdEvent.Call(vk, 0, KEYUP, 0) // up
	return map[string]interface{}{"ok": true, "vk": int(argNum(a, "vk", 0))}, nil
}

func toolPixel(a map[string]interface{}) (interface{}, error) {
	x, y := int(argNum(a, "x", 0)), int(argNum(a, "y", 0))
	dc, _, _ := pGetWindowDC.Call(0) // screen DC
	c, _, _ := pGetPixel.Call(dc, uintptr(x), uintptr(y))
	pReleaseDC.Call(0, dc)
	r, g, b := int(c&0xff), int((c>>8)&0xff), int((c>>16)&0xff)
	return map[string]interface{}{"x": x, "y": y, "r": r, "g": g, "b": b,
		"hex": fmt.Sprintf("#%02x%02x%02x", r, g, b)}, nil
}

type moduleEntry32 struct {
	Size, ModuleID, ProcessID, GlblcntUsage, ProccntUsage uint32
	ModBaseAddr                                           uintptr
	ModBaseSize                                           uint32
	HModule                                               uintptr
	Module                                                [256]uint16
	ExePath                                               [260]uint16
}

func toolProcessModules(a map[string]interface{}) (interface{}, error) {
	pid := uint32(argNum(a, "pid", 0))
	const snapModule = 0x00000008 | 0x00000010 // MODULE | MODULE32
	snap, _, _ := pCreateToolhelp32Snapshot.Call(snapModule, uintptr(pid))
	if int(snap) == -1 {
		return nil, fmt.Errorf("snapshot failed for pid %d", pid)
	}
	defer pCloseHandle.Call(snap)
	var me moduleEntry32
	me.Size = uint32(unsafe.Sizeof(me))
	mods := []map[string]interface{}{}
	r, _, _ := pModule32FirstW.Call(snap, uintptr(unsafe.Pointer(&me)))
	for r != 0 {
		mods = append(mods, map[string]interface{}{
			"name": syscall.UTF16ToString(me.Module[:]),
			"path": syscall.UTF16ToString(me.ExePath[:]),
			"size": me.ModBaseSize,
		})
		if len(mods) >= 500 {
			break
		}
		r, _, _ = pModule32NextW.Call(snap, uintptr(unsafe.Pointer(&me)))
	}
	return map[string]interface{}{"pid": pid, "count": len(mods), "modules": mods}, nil
}

func toolKillProcess(a map[string]interface{}) (interface{}, error) {
	pid := uint32(argNum(a, "pid", 0))
	const terminate = 0x0001 // PROCESS_TERMINATE
	h, _, _ := pOpenProcess.Call(terminate, 0, uintptr(pid))
	if h == 0 {
		return nil, fmt.Errorf("could not open pid %d", pid)
	}
	defer pCloseHandle.Call(h)
	ok, _, _ := pTerminateProcess.Call(h, 1)
	return map[string]interface{}{"pid": pid, "terminated": ok != 0}, nil
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// --- screenshot (full desktop) ---

type bitmapInfoHeader struct {
	Size                                                  uint32
	Width, Height                                         int32
	Planes, BitCount                                      uint16
	Compression, SizeImage                                uint32
	XPelsPerMeter, YPelsPerMeter                          int32
	ClrUsed, ClrImportant                                 uint32
}

func toolScreenshot(a map[string]interface{}) (interface{}, error) {
	const smCX, smCY, smXV, smYV = 0, 1, 76, 77
	x, _, _ := pGetSystemMetrics.Call(smXV)
	y, _, _ := pGetSystemMetrics.Call(smYV)
	rw, _, _ := pGetSystemMetrics.Call(78) // CXVIRTUALSCREEN
	rh, _, _ := pGetSystemMetrics.Call(79) // CYVIRTUALSCREEN
	xi, yi, w, h := int(int32(x)), int(int32(y)), int(int32(rw)), int(int32(rh))
	if w == 0 || h == 0 {
		cw, _, _ := pGetSystemMetrics.Call(smCX)
		ch, _, _ := pGetSystemMetrics.Call(smCY)
		w, h = int(int32(cw)), int(int32(ch))
	}

	desktop, _, _ := pGetDesktopWindow.Call()
	srcDC, _, _ := pGetWindowDC.Call(desktop)
	memDC, _, _ := pCreateCompatibleDC.Call(srcDC)
	bmp, _, _ := pCreateCompatibleBitmap.Call(srcDC, uintptr(w), uintptr(h))
	pSelectObject.Call(memDC, bmp)
	pBitBlt.Call(memDC, 0, 0, uintptr(w), uintptr(h), srcDC, uintptr(xi), uintptr(yi), 0x00CC0020)

	row := (w*3 + 3) &^ 3
	imgSize := row * h
	pixels := make([]byte, imgSize)
	bi := bitmapInfoHeader{Size: 40, Width: int32(w), Height: int32(h), Planes: 1, BitCount: 24}
	pGetDIBits.Call(memDC, bmp, 0, uintptr(h), uintptr(unsafe.Pointer(&pixels[0])), uintptr(unsafe.Pointer(&bi)), 0)
	pDeleteObject.Call(bmp)
	pDeleteDC.Call(memDC)
	pReleaseDC.Call(desktop, srcDC)

	var out bytes.Buffer
	out.WriteString("BM")
	binary.Write(&out, binary.LittleEndian, uint32(14+40+imgSize))
	binary.Write(&out, binary.LittleEndian, uint16(0))
	binary.Write(&out, binary.LittleEndian, uint16(0))
	binary.Write(&out, binary.LittleEndian, uint32(14+40))
	binary.Write(&out, binary.LittleEndian, bi)
	out.Write(pixels)
	return imageResult{Data: base64.StdEncoding.EncodeToString(out.Bytes()), Mime: "image/bmp"}, nil
}
