# Claude MCP Server

Exposes Claude agent as a tool for other agents to invoke.

## Description

This MCP server allows other agents (Codex, Gemini) to invoke Claude for tasks requiring strong reasoning, architecture design, and long-context capabilities.

## Agent Restrictions

- **Allowed for**: Codex, Gemini
- **Blocked for**: Claude (cannot invoke itself)

## Installation

```bash
# Automatically installs to allowed agents (Codex and Gemini)
abox mcp add claude-mcp
```

## Usage

When Claude MCP is installed in Codex or Gemini, those agents gain access to the `invoke_claude` tool:

```
invoke_claude(
  task: "Design the architecture for a user authentication system",
  timeout: 300  # optional, in seconds
)
```

## Tool: invoke_claude

**Parameters:**
- `task` (required): The task prompt to send to Claude
- `timeout` (optional): Timeout in seconds (no timeout by default)

**Returns:**
- Success: Claude's response as text
- Error: Error message if invocation failed

## Use Cases

### 1. Architecture Design
```
invoke_claude(task="Design a scalable microservices architecture for our e-commerce platform")
```

### 2. Complex Problem Solving
```
invoke_claude(task="Analyze this algorithm and suggest optimizations for O(n²) complexity")
```

### 3. Code Review
```
invoke_claude(task="Review this authentication implementation for security best practices")
```

### 4. Documentation
```
invoke_claude(task="Write comprehensive API documentation for these endpoints")
```

## Technical Details

### One-Shot Execution
- Claude executes the task autonomously without user interaction
- Returns result or error to the calling agent
- No nested invocations (Claude cannot invoke other agents)

### Full Workspace Access
- Invoked Claude has full read/write access to the workspace
- Can use all MCP servers configured for Claude
- Same environment and context as interactive Claude

### No Nesting
- Invocation depth is limited to 1
- If Claude is invoked by another agent, it cannot invoke other agents
- Prevents infinite loops and complexity

## Example Workflow

**Codex writes code, asks Claude for review:**

1. Codex implements a feature
2. Codex calls `invoke_claude(task="Review this implementation: <code>")`
3. Claude executes the review autonomously
4. Claude returns feedback
5. Codex continues with Claude's feedback

## Environment Variables

The MCP server automatically sets:
- `AGENTBOX_INVOCATION_DEPTH=1`: Marks Claude as being invoked
- `AGENTBOX_CONTAINER_NAME`: Target container name (inherited)

## Error Handling

**Common errors:**
- `"Nested agent invocations not allowed"`: Attempted to invoke from already-invoked agent
- `"Claude task timed out after X seconds"`: Task exceeded timeout
- `"Claude invocation failed: <error>"`: General execution error

Errors are returned to the calling agent, not to the user.

## Notes

- This is designed for autonomous (super) agent communication
- For interactive workflows, use the standard `abox claude` command
- Invocations are logged but do not create desktop notifications by default
