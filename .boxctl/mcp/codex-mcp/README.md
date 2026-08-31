# Codex MCP Server

Exposes Codex agent as a tool for other agents to invoke.

## Description

This MCP server allows other agents (Claude, Gemini) to invoke Codex for tasks requiring code generation, implementation, and validation.

## Agent Restrictions

- **Allowed for**: Claude, Gemini
- **Blocked for**: Codex (cannot invoke itself)

## Installation

```bash
# Automatically installs to allowed agents (Claude and Gemini)
abox mcp add codex-mcp
```

## Usage

When Codex MCP is installed in Claude or Gemini, those agents gain access to the `invoke_codex` tool:

```
invoke_codex(
  task: "Implement a binary search tree with insert and delete methods",
  timeout: 300  # optional, in seconds
)
```

## Tool: invoke_codex

**Parameters:**
- `task` (required): The task prompt to send to Codex
- `timeout` (optional): Timeout in seconds (no timeout by default)

**Returns:**
- Success: Codex's response as text
- Error: Error message if invocation failed

## Use Cases

### 1. Code Implementation
```
invoke_codex(task="Implement the authentication middleware based on this design document")
```

### 2. Code Validation
```
invoke_codex(task="Validate this implementation against the requirements and check for bugs")
```

### 3. Testing
```
invoke_codex(task="Write comprehensive unit tests for this authentication module")
```

### 4. Refactoring
```
invoke_codex(task="Refactor this code to follow DRY principles and improve readability")
```

### 5. Quick Fixes
```
invoke_codex(task="Fix the type errors in this TypeScript code")
```

## Technical Details

### One-Shot Execution
- Codex executes the task autonomously without user interaction
- Returns result or error to the calling agent
- No nested invocations (Codex cannot invoke other agents)

### Full Workspace Access
- Invoked Codex has full read/write access to the workspace
- Can use all MCP servers configured for Codex
- Same environment and context as interactive Codex

### No Nesting
- Invocation depth is limited to 1
- If Codex is invoked by another agent, it cannot invoke other agents
- Prevents infinite loops and complexity

## Example Workflow

**Claude designs, asks Codex to implement:**

1. Claude designs system architecture
2. Claude calls `invoke_codex(task="Implement this architecture: <design>")`
3. Codex executes the implementation autonomously
4. Codex returns the code
5. Claude validates and continues

**Common pattern - Claude writes tests, Codex validates:**

1. Claude writes unit tests
2. Claude calls `invoke_codex(task="Review these tests and validate coverage")`
3. Codex provides feedback
4. Claude improves tests based on feedback

## Environment Variables

The MCP server automatically sets:
- `AGENTBOX_INVOCATION_DEPTH=1`: Marks Codex as being invoked
- `AGENTBOX_CONTAINER_NAME`: Target container name (inherited)

## Error Handling

**Common errors:**
- `"Nested agent invocations not allowed"`: Attempted to invoke from already-invoked agent
- `"Codex task timed out after X seconds"`: Task exceeded timeout
- `"Codex invocation failed: <error>"`: General execution error

Errors are returned to the calling agent, not to the user.

## Notes

- This is designed for autonomous (super) agent communication
- For interactive workflows, use the standard `abox codex` command
- Invocations are logged but do not create desktop notifications by default
- Codex excels at code generation - leverage this for implementation tasks
