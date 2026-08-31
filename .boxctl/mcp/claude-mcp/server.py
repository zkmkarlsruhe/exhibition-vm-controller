#!/usr/bin/env python3
# Copyright (c) 2025 Marc Schütze <scharc@gmail.com>
# SPDX-License-Identifier: MIT
# See LICENSE file in the project root for full license information.

"""Claude agent MCP server - exposes Claude to other agents."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional


SERVER_NAME = "claude-mcp"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


class MessageIO:
    """Handles MCP protocol message I/O."""

    def __init__(self) -> None:
        self._use_content_length: Optional[bool] = None

    def read(self) -> Optional[Dict[str, Any]]:
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None

            if line.startswith(b"Content-Length:"):
                self._use_content_length = True
                try:
                    length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    return None

                while True:
                    header = sys.stdin.buffer.readline()
                    if not header or header in (b"\r\n", b"\n"):
                        break

                body = sys.stdin.buffer.read(length)
                if not body:
                    return None
                return self._parse_json(body)

            stripped = line.strip()
            if not stripped:
                continue

            if self._use_content_length is None:
                self._use_content_length = False
            return self._parse_json(stripped)

    def write(self, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self._use_content_length:
            header = f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8")
            sys.stdout.buffer.write(header + raw)
        else:
            sys.stdout.buffer.write(raw + b"\n")
        sys.stdout.buffer.flush()

    @staticmethod
    def _parse_json(raw: bytes) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        if isinstance(data, dict):
            return data
        return None


def _invoke_claude(task: str, timeout: Optional[int] = None) -> Dict[str, Any]:
    """Invoke Claude agent in one-shot mode.

    Args:
        task: Task prompt for Claude
        timeout: Optional timeout in seconds

    Returns:
        Dict with success, output, and optional error
    """
    # Check invocation depth to prevent nesting
    depth = int(os.getenv("AGENTBOX_INVOCATION_DEPTH", "0"))
    if depth > 0:
        return {
            "success": False,
            "error": "Nested agent invocations not allowed",
            "output": ""
        }

    # Get container name
    container_name = os.getenv("AGENTBOX_CONTAINER_NAME", "agentbox-workspace")

    # Build command for one-shot execution
    cmd = [
        "docker", "exec",
        "-e", "AGENTBOX_INVOCATION_DEPTH=1",  # Mark as invoked
        container_name,
        "claude", "exec",
        "-p", task
    ]

    try:
        # Execute with optional timeout
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() if result.returncode != 0 else None,
            "exit_code": result.returncode,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Claude task timed out after {timeout} seconds",
            "output": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "output": ""
        }


def _tool_spec() -> Dict[str, Any]:
    """Return tool specification."""
    return {
        "name": "invoke_claude",
        "description": (
            "Invoke Claude agent to perform a task. "
            "Claude has strong reasoning, architecture design, and long-context capabilities. "
            "Use Claude for: design decisions, complex problem solving, documentation, code review. "
            "The invocation is one-shot: Claude executes the task autonomously and returns the result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task prompt to send to Claude"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Optional timeout in seconds (no timeout by default)"
                }
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    }


def _success_response(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _handle_initialize(params: Dict[str, Any]) -> Dict[str, Any]:
    protocol = params.get("protocolVersion") if isinstance(params, dict) else None
    return {
        "protocolVersion": protocol or DEFAULT_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def _handle_tools_list() -> Dict[str, Any]:
    return {"tools": [_tool_spec()]}


def _handle_tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tools/call request."""
    if not isinstance(params, dict):
        return {"content": [{"type": "text", "text": "Invalid parameters"}], "isError": True}

    name = params.get("name")
    arguments = params.get("arguments") or {}

    if name != "invoke_claude":
        return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}

    if not isinstance(arguments, dict):
        return {"content": [{"type": "text", "text": "Invalid tool arguments"}], "isError": True}

    task = arguments.get("task")
    timeout = arguments.get("timeout")

    if not task:
        return {
            "content": [{"type": "text", "text": "Missing required parameter: task"}],
            "isError": True
        }

    result = _invoke_claude(task, timeout)

    if not result["success"]:
        error_msg = result.get("error", "Unknown error")
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Claude invocation failed: {error_msg}"
                }
            ],
            "isError": True
        }

    return {
        "content": [
            {
                "type": "text",
                "text": f"Claude response:\n\n{result['output']}"
            }
        ]
    }


def main() -> None:
    """Main MCP server loop."""
    io = MessageIO()

    while True:
        request = io.read()
        if request is None:
            break

        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params")

        if method == "initialize":
            result = _handle_initialize(params if isinstance(params, dict) else {})
            io.write(_success_response(request_id, result))
            continue

        if method == "tools/list":
            io.write(_success_response(request_id, _handle_tools_list()))
            continue

        if method == "tools/call":
            io.write(_success_response(request_id, _handle_tools_call(params)))
            continue

        if request_id is None:
            continue

        io.write(_error_response(request_id, -32601, "Method not found"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
