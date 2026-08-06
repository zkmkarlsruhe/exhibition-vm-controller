// Non-Windows stub so the cross-platform code builds and can be tested on a
// Linux dev box. The Win32 tools (screenshot, list_windows, input, etc.) live
// in winapi_windows.go and self-register only on Windows.

//go:build !windows
// +build !windows

package main

import (
	"context"
	"os/exec"
)

func shellCommand(ctx context.Context, command string) *exec.Cmd {
	return exec.CommandContext(ctx, "/bin/sh", "-c", command)
}
