// legacy_agent — in-guest MCP agent for legacy Windows, as a single static exe.
//
// A Go rewrite of legacy_agent.py. It speaks the MCP wire protocol directly
// (JSON-RPC 2.0 over a newline-delimited stream) and ships as one dependency-
// free executable, so nothing needs installing in the guest. Cross-compiled
// from Linux for Windows XP:
//
//	GOOS=windows GOARCH=386 CGO_ENABLED=0 go build -o legacy-agent.exe .
//
// A windows/386 binary runs XP -> Windows 10. Build with the Go 1.10 toolchain
// for officially-supported XP output, or modern Go (386 cross-builds run on XP
// in practice). The code sticks to <=1.10 stdlib APIs so either works.
//
// Transport: TCP server with stdio framing; bridge with
//
//	claude mcp add legacy-xp -- socat - TCP:192.168.122.50:9009
//
// NO AUTH — trusted museum LAN only, same posture as Ghost Commander.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/md5"
	"crypto/sha1"
	"encoding/base64"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"io/ioutil"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"syscall"
	"time"
)

const agentVersion = "0.1.0"

// --- JSON-RPC types ---

type rpcRequest struct {
	JSONRPC string           `json:"jsonrpc"`
	ID      *json.RawMessage `json:"id"`
	Method  string           `json:"method"`
	Params  json.RawMessage  `json:"params"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type rpcResponse struct {
	JSONRPC string           `json:"jsonrpc"`
	ID      *json.RawMessage `json:"id"`
	Result  interface{}      `json:"result,omitempty"`
	Error   *rpcError        `json:"error,omitempty"`
}

type toolContent struct {
	Type     string `json:"type"`
	Text     string `json:"text,omitempty"`
	Data     string `json:"data,omitempty"`
	MimeType string `json:"mimeType,omitempty"`
}

type toolResult struct {
	Content []toolContent `json:"content"`
	IsError bool          `json:"isError"`
}

// imageResult is returned by platform screenshot handlers; the dispatcher
// turns it into MCP image content.
type imageResult struct {
	Data string
	Mime string
}

type toolDef struct {
	desc    string
	schema  map[string]interface{}
	handler func(map[string]interface{}) (interface{}, error)
}

// --- arg helpers ---

func argStr(a map[string]interface{}, k, def string) string {
	if v, ok := a[k]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return def
}

func argNum(a map[string]interface{}, k string, def float64) float64 {
	if v, ok := a[k]; ok {
		if f, ok := v.(float64); ok {
			return f
		}
	}
	return def
}

func argBool(a map[string]interface{}, k string) bool {
	if v, ok := a[k]; ok {
		if b, ok := v.(bool); ok {
			return b
		}
	}
	return false
}

func objSchema(props map[string]interface{}, required ...string) map[string]interface{} {
	if props == nil {
		props = map[string]interface{}{}
	}
	if required == nil {
		required = []string{}
	}
	return map[string]interface{}{"type": "object", "properties": props, "required": required}
}

// --- tools ---

func toolSystemInfo(a map[string]interface{}) (interface{}, error) {
	host, _ := os.Hostname()
	wd, _ := os.Getwd()
	return map[string]interface{}{
		"os":         runtime.GOOS,
		"arch":       runtime.GOARCH,
		"hostname":   host,
		"cwd":        wd,
		"go_version": runtime.Version(),
		"is_windows": runtime.GOOS == "windows",
	}, nil
}

func toolRunShell(a map[string]interface{}) (interface{}, error) {
	command := argStr(a, "command", "")
	if command == "" {
		return nil, fmt.Errorf("command is required")
	}
	timeout := time.Duration(argNum(a, "timeout", 30)) * time.Second
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	cmd := shellCommand(ctx, command)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()

	res := map[string]interface{}{
		"stdout": stdout.String(),
		"stderr": stderr.String(),
	}
	if ctx.Err() == context.DeadlineExceeded {
		res["timed_out"] = true
		res["exit_code"] = nil
	} else {
		res["timed_out"] = false
		res["exit_code"] = exitCodeOf(err)
	}
	return res, nil
}

func exitCodeOf(err error) interface{} {
	if err == nil {
		return 0
	}
	if ee, ok := err.(*exec.ExitError); ok {
		if ws, ok := ee.Sys().(syscall.WaitStatus); ok {
			return ws.ExitStatus()
		}
	}
	return -1
}

func toolListProcesses(a map[string]interface{}) (interface{}, error) {
	procs := []map[string]interface{}{}
	if runtime.GOOS == "windows" {
		out, _ := exec.Command("tasklist", "/fo", "csv", "/nh").Output()
		r := csv.NewReader(bytes.NewReader(out))
		r.FieldsPerRecord = -1
		rows, _ := r.ReadAll()
		for _, row := range rows {
			if len(row) >= 5 {
				procs = append(procs, map[string]interface{}{"name": row[0], "pid": row[1], "mem": row[4]})
			}
		}
	} else {
		out, _ := exec.Command("ps", "-eo", "pid,comm,rss").Output()
		lines := strings.Split(string(out), "\n")
		for _, ln := range lines[1:] {
			f := strings.Fields(ln)
			if len(f) >= 2 {
				p := map[string]interface{}{"pid": f[0], "name": f[1]}
				if len(f) > 2 {
					p["mem"] = f[2]
				}
				procs = append(procs, p)
			}
		}
	}
	return map[string]interface{}{"count": len(procs), "processes": procs}, nil
}

func toolListDir(a map[string]interface{}) (interface{}, error) {
	path := argStr(a, "path", "")
	if path == "" {
		return nil, fmt.Errorf("path is required")
	}
	infos, err := ioutil.ReadDir(path)
	if err != nil {
		return nil, err
	}
	entries := []map[string]interface{}{}
	for _, fi := range infos {
		e := map[string]interface{}{"name": fi.Name(), "is_dir": fi.IsDir()}
		if !fi.IsDir() {
			e["size"] = fi.Size()
		}
		entries = append(entries, e)
	}
	return map[string]interface{}{"path": path, "count": len(entries), "entries": entries}, nil
}

func toolReadFile(a map[string]interface{}) (interface{}, error) {
	path := argStr(a, "path", "")
	if path == "" {
		return nil, fmt.Errorf("path is required")
	}
	maxBytes := int64(argNum(a, "max_bytes", 1024*1024))
	offset := int64(argNum(a, "offset", 0))
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	if offset > 0 {
		f.Seek(offset, 0)
	}
	data, err := ioutil.ReadAll(io.LimitReader(f, maxBytes))
	if err != nil {
		return nil, err
	}
	res := map[string]interface{}{"path": path, "bytes": len(data), "offset": offset}
	if argBool(a, "binary") {
		res["base64"] = base64.StdEncoding.EncodeToString(data)
	} else {
		res["text"] = string(data)
	}
	return res, nil
}

func toolWriteFile(a map[string]interface{}) (interface{}, error) {
	path := argStr(a, "path", "")
	if path == "" {
		return nil, fmt.Errorf("path is required")
	}
	content := argStr(a, "content", "")
	var data []byte
	if argBool(a, "base64") {
		b, err := base64.StdEncoding.DecodeString(content)
		if err != nil {
			return nil, err
		}
		data = b
	} else {
		data = []byte(content)
	}
	if err := ioutil.WriteFile(path, data, 0644); err != nil {
		return nil, err
	}
	return map[string]interface{}{"path": path, "bytes": len(data)}, nil
}

func toolEnv(a map[string]interface{}) (interface{}, error) {
	env := map[string]string{}
	for _, e := range os.Environ() {
		if i := strings.IndexByte(e, '='); i > 0 {
			env[e[:i]] = e[i+1:]
		}
	}
	return env, nil
}

func toolDeleteFile(a map[string]interface{}) (interface{}, error) {
	path := argStr(a, "path", "")
	if path == "" {
		return nil, fmt.Errorf("path is required")
	}
	if err := os.Remove(path); err != nil {
		return nil, err
	}
	return map[string]interface{}{"deleted": path}, nil
}

func toolMoveFile(a map[string]interface{}) (interface{}, error) {
	from, to := argStr(a, "from", ""), argStr(a, "to", "")
	if from == "" || to == "" {
		return nil, fmt.Errorf("from and to are required")
	}
	if err := os.Rename(from, to); err != nil {
		return nil, err
	}
	return map[string]interface{}{"from": from, "to": to}, nil
}

func toolMakeDir(a map[string]interface{}) (interface{}, error) {
	path := argStr(a, "path", "")
	if path == "" {
		return nil, fmt.Errorf("path is required")
	}
	if err := os.MkdirAll(path, 0755); err != nil {
		return nil, err
	}
	return map[string]interface{}{"created": path}, nil
}

func toolFindFiles(a map[string]interface{}) (interface{}, error) {
	root := argStr(a, "dir", "")
	pattern := argStr(a, "pattern", "*")
	limit := int(argNum(a, "limit", 200))
	if root == "" {
		return nil, fmt.Errorf("dir is required")
	}
	matches := []string{}
	filepath.Walk(root, func(p string, fi os.FileInfo, err error) error {
		if err != nil || fi.IsDir() {
			return nil
		}
		if ok, _ := filepath.Match(pattern, fi.Name()); ok {
			matches = append(matches, p)
			if len(matches) >= limit {
				return filepath.SkipDir
			}
		}
		return nil
	})
	return map[string]interface{}{"dir": root, "pattern": pattern, "count": len(matches), "files": matches}, nil
}

func toolFileHash(a map[string]interface{}) (interface{}, error) {
	path := argStr(a, "path", "")
	if path == "" {
		return nil, fmt.Errorf("path is required")
	}
	data, err := ioutil.ReadFile(path)
	if err != nil {
		return nil, err
	}
	m, s := md5.Sum(data), sha1.Sum(data)
	return map[string]interface{}{"path": path, "bytes": len(data),
		"md5": hex.EncodeToString(m[:]), "sha1": hex.EncodeToString(s[:])}, nil
}

func toolHTTPGet(a map[string]interface{}) (interface{}, error) {
	url := argStr(a, "url", "")
	if url == "" {
		return nil, fmt.Errorf("url is required")
	}
	maxBytes := int64(argNum(a, "max_bytes", 65536))
	client := &http.Client{Timeout: time.Duration(argNum(a, "timeout", 15)) * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := ioutil.ReadAll(io.LimitReader(resp.Body, maxBytes))
	hdr := map[string]string{}
	for k := range resp.Header {
		hdr[k] = resp.Header.Get(k)
	}
	return map[string]interface{}{"url": url, "status": resp.StatusCode,
		"headers": hdr, "bytes": len(body), "body": string(body)}, nil
}

func toolDNSLookup(a map[string]interface{}) (interface{}, error) {
	host := argStr(a, "host", "")
	if host == "" {
		return nil, fmt.Errorf("host is required")
	}
	addrs, err := net.LookupHost(host)
	if err != nil {
		return map[string]interface{}{"host": host, "error": err.Error(), "addrs": []string{}}, nil
	}
	return map[string]interface{}{"host": host, "addrs": addrs}, nil
}

func toolTCPProbe(a map[string]interface{}) (interface{}, error) {
	host := argStr(a, "host", "")
	port := int(argNum(a, "port", 0))
	if host == "" || port == 0 {
		return nil, fmt.Errorf("host and port are required")
	}
	addr := fmt.Sprintf("%s:%d", host, port)
	conn, err := net.DialTimeout("tcp", addr, time.Duration(argNum(a, "timeout", 5))*time.Second)
	if err != nil {
		return map[string]interface{}{"target": addr, "open": false, "error": err.Error()}, nil
	}
	conn.Close()
	return map[string]interface{}{"target": addr, "open": true}, nil
}

var tools = map[string]toolDef{
	"system_info": {
		desc:    "OS, hostname, arch and cwd of the guest.",
		schema:  objSchema(nil),
		handler: toolSystemInfo,
	},
	"run_shell": {
		desc: "Run a shell/cmd command in the guest; returns stdout, stderr, exit code.",
		schema: objSchema(map[string]interface{}{
			"command": map[string]interface{}{"type": "string"},
			"timeout": map[string]interface{}{"type": "number", "description": "seconds (default 30)"},
		}, "command"),
		handler: toolRunShell,
	},
	"list_processes": {
		desc:    "List running processes (name, pid, memory).",
		schema:  objSchema(nil),
		handler: toolListProcesses,
	},
	"list_dir": {
		desc: "List a directory's entries (name, is_dir, size).",
		schema: objSchema(map[string]interface{}{
			"path": map[string]interface{}{"type": "string"},
		}, "path"),
		handler: toolListDir,
	},
	"read_file": {
		desc: "Read a file from the guest (text by default; set binary=true for base64).",
		schema: objSchema(map[string]interface{}{
			"path":      map[string]interface{}{"type": "string"},
			"max_bytes": map[string]interface{}{"type": "number"},
			"offset":    map[string]interface{}{"type": "number", "description": "byte offset (for tailing logs)"},
			"binary":    map[string]interface{}{"type": "boolean"},
		}, "path"),
		handler: toolReadFile,
	},
	"write_file": {
		desc: "Write a file in the guest (utf-8 text, or base64 if base64=true).",
		schema: objSchema(map[string]interface{}{
			"path":    map[string]interface{}{"type": "string"},
			"content": map[string]interface{}{"type": "string"},
			"base64":  map[string]interface{}{"type": "boolean"},
		}, "path", "content"),
		handler: toolWriteFile,
	},
	"env": {
		desc:    "Return the guest's environment variables.",
		schema:  objSchema(nil),
		handler: toolEnv,
	},
	"delete_file": {
		desc:    "Delete a file in the guest.",
		schema:  objSchema(map[string]interface{}{"path": map[string]interface{}{"type": "string"}}, "path"),
		handler: toolDeleteFile,
	},
	"move_file": {
		desc: "Move/rename a file in the guest.",
		schema: objSchema(map[string]interface{}{
			"from": map[string]interface{}{"type": "string"},
			"to":   map[string]interface{}{"type": "string"}}, "from", "to"),
		handler: toolMoveFile,
	},
	"make_dir": {
		desc:    "Create a directory (and parents) in the guest.",
		schema:  objSchema(map[string]interface{}{"path": map[string]interface{}{"type": "string"}}, "path"),
		handler: toolMakeDir,
	},
	"find_files": {
		desc: "Recursively find files under a directory matching a glob pattern.",
		schema: objSchema(map[string]interface{}{
			"dir":     map[string]interface{}{"type": "string"},
			"pattern": map[string]interface{}{"type": "string", "description": "glob, e.g. *.dir"},
			"limit":   map[string]interface{}{"type": "number"}}, "dir"),
		handler: toolFindFiles,
	},
	"file_hash": {
		desc:    "MD5 + SHA1 of a file (identify/verify a binary or asset).",
		schema:  objSchema(map[string]interface{}{"path": map[string]interface{}{"type": "string"}}, "path"),
		handler: toolFileHash,
	},
	"http_get": {
		desc: "HTTP GET a URL THROUGH the guest's own network stack — see what the artwork's call returns (dead server? proxy reply?).",
		schema: objSchema(map[string]interface{}{
			"url":       map[string]interface{}{"type": "string"},
			"max_bytes": map[string]interface{}{"type": "number"},
			"timeout":   map[string]interface{}{"type": "number"}}, "url"),
		handler: toolHTTPGet,
	},
	"dns_lookup": {
		desc:    "Resolve a hostname from inside the guest (uses the guest's DNS).",
		schema:  objSchema(map[string]interface{}{"host": map[string]interface{}{"type": "string"}}, "host"),
		handler: toolDNSLookup,
	},
	"tcp_probe": {
		desc: "Test whether the guest can open a TCP connection to host:port.",
		schema: objSchema(map[string]interface{}{
			"host":    map[string]interface{}{"type": "string"},
			"port":    map[string]interface{}{"type": "number"},
			"timeout": map[string]interface{}{"type": "number"}}, "host", "port"),
		handler: toolTCPProbe,
	},
}

func toolsList() []map[string]interface{} {
	names := make([]string, 0, len(tools))
	for n := range tools {
		names = append(names, n)
	}
	sort.Strings(names)
	out := make([]map[string]interface{}, 0, len(names))
	for _, n := range names {
		out = append(out, map[string]interface{}{
			"name": n, "description": tools[n].desc, "inputSchema": tools[n].schema,
		})
	}
	return out
}

func callTool(name string, args map[string]interface{}) toolResult {
	td, ok := tools[name]
	if !ok {
		return toolResult{Content: []toolContent{{Type: "text", Text: "unknown tool: " + name}}, IsError: true}
	}
	val, err := td.handler(args)
	if err != nil {
		return toolResult{Content: []toolContent{{Type: "text", Text: "tool error: " + err.Error()}}, IsError: true}
	}
	if img, ok := val.(imageResult); ok {
		return toolResult{Content: []toolContent{{Type: "image", Data: img.Data, MimeType: img.Mime}}, IsError: false}
	}
	b, _ := json.MarshalIndent(val, "", "  ")
	return toolResult{Content: []toolContent{{Type: "text", Text: string(b)}}, IsError: false}
}

// --- protocol dispatch ---

func respondResult(id *json.RawMessage, result interface{}) []byte {
	b, _ := json.Marshal(rpcResponse{JSONRPC: "2.0", ID: id, Result: result})
	return b
}

func respondError(id *json.RawMessage, code int, msg string) []byte {
	b, _ := json.Marshal(rpcResponse{JSONRPC: "2.0", ID: id, Error: &rpcError{Code: code, Message: msg}})
	return b
}

func handleRequest(req rpcRequest) []byte {
	switch {
	case req.Method == "initialize":
		var p struct {
			ProtocolVersion string `json:"protocolVersion"`
		}
		json.Unmarshal(req.Params, &p)
		pv := p.ProtocolVersion
		if pv == "" {
			pv = "2024-11-05"
		}
		return respondResult(req.ID, map[string]interface{}{
			"protocolVersion": pv,
			"capabilities":    map[string]interface{}{"tools": map[string]interface{}{}},
			"serverInfo":      map[string]interface{}{"name": "legacy-guest-agent", "version": agentVersion},
		})
	case req.Method == "ping":
		return respondResult(req.ID, struct{}{})
	case req.Method == "tools/list":
		return respondResult(req.ID, map[string]interface{}{"tools": toolsList()})
	case req.Method == "tools/call":
		var p struct {
			Name      string                 `json:"name"`
			Arguments map[string]interface{} `json:"arguments"`
		}
		json.Unmarshal(req.Params, &p)
		if p.Arguments == nil {
			p.Arguments = map[string]interface{}{}
		}
		return respondResult(req.ID, callTool(p.Name, p.Arguments))
	case strings.HasPrefix(req.Method, "notifications/"):
		return nil
	default:
		if req.ID == nil {
			return nil
		}
		return respondError(req.ID, -32601, "method not found: "+req.Method)
	}
}

func processLine(line []byte) []byte {
	line = bytes.TrimSpace(line)
	if len(line) == 0 {
		return nil
	}
	var req rpcRequest
	if err := json.Unmarshal(line, &req); err != nil {
		return respondError(nil, -32700, "parse error")
	}
	return handleRequest(req)
}

// --- transports ---

func handleConn(conn net.Conn) {
	defer conn.Close()
	r := bufio.NewReader(conn)
	for {
		line, err := r.ReadBytes('\n')
		if len(line) > 0 {
			if resp := processLine(line); resp != nil {
				conn.Write(append(resp, '\n'))
			}
		}
		if err != nil {
			return
		}
	}
}

func serveStdio() {
	r := bufio.NewReader(os.Stdin)
	for {
		line, err := r.ReadBytes('\n')
		if len(line) > 0 {
			if resp := processLine(line); resp != nil {
				os.Stdout.Write(append(resp, '\n'))
			}
		}
		if err != nil {
			return
		}
	}
}

func main() {
	host := flag.String("host", "0.0.0.0", "bind address")
	port := flag.Int("port", 9009, "TCP port")
	stdio := flag.Bool("stdio", false, "run over stdin/stdout instead of TCP")
	flag.Parse()

	if *stdio {
		serveStdio()
		return
	}

	addr := fmt.Sprintf("%s:%d", *host, *port)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "listen failed: %v\n", err)
		os.Exit(1)
	}
	fmt.Fprintf(os.Stderr, "legacy-guest-agent %s listening on %s\n", agentVersion, addr)
	for {
		conn, err := ln.Accept()
		if err != nil {
			continue
		}
		go handleConn(conn)
	}
}
