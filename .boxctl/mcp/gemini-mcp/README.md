# Gemini MCP Server

Exposes Gemini agent as a tool for other agents to invoke.

## Description

This MCP server allows other agents (Claude, Codex) to invoke Gemini for tasks requiring research, comprehensive analysis, testing, and cross-validation.

## Agent Restrictions

- **Allowed for**: Claude, Codex
- **Blocked for**: Gemini (cannot invoke itself)

## Installation

```bash
# Automatically installs to allowed agents (Claude and Codex)
abox mcp add gemini-mcp
```

## Usage

When Gemini MCP is installed in Claude or Codex, those agents gain access to the `invoke_gemini` tool:

```
invoke_gemini(
  task: "Research best practices for JWT token security in 2026",
  timeout: 300  # optional, in seconds
)
```

## Tool: invoke_gemini

**Parameters:**
- `task` (required): The task prompt to send to Gemini
- `timeout` (optional): Timeout in seconds (no timeout by default)

**Returns:**
- Success: Gemini's response as text
- Error: Error message if invocation failed

## Use Cases

### 1. Cross-Validation
```
invoke_gemini(task="Validate this implementation and check if it matches the requirements")
```

### 2. Research
```
invoke_gemini(task="Research current best practices for API rate limiting in 2026")
```

### 3. Comprehensive Testing
```
invoke_gemini(task="Create a comprehensive test suite including edge cases and error scenarios")
```

### 4. Analysis
```
invoke_gemini(task="Analyze this codebase and identify potential performance bottlenecks")
```

### 5. Arbitration
```
invoke_gemini(task="Compare these two approaches and recommend which one to use: <options>")
```

## Technical Details

### One-Shot Execution
- Gemini executes the task autonomously without user interaction
- Returns result or error to the calling agent
- No nested invocations (Gemini cannot invoke other agents)

### Full Workspace Access
- Invoked Gemini has full read/write access to the workspace
- Can use all MCP servers configured for Gemini
- Same environment and context as interactive Gemini

### No Nesting
- Invocation depth is limited to 1
- If Gemini is invoked by another agent, it cannot invoke other agents
- Prevents infinite loops and complexity

## Example Workflows

### Cross-Validation Pattern
1. Claude implements a feature
2. Claude calls `invoke_gemini(task="Validate this implementation: <code>")`
3. Gemini performs thorough validation
4. Gemini returns findings
5. Claude addresses any issues

### Research and Implementation
1. Codex needs to implement a feature
2. Codex calls `invoke_gemini(task="Research best practices for OAuth2 implementation")`
3. Gemini provides comprehensive research
4. Codex implements based on research

### Testing and Validation
1. Codex writes tests
2. Codex calls `invoke_gemini(task="Review test coverage and suggest additional test cases")`
3. Gemini provides detailed feedback
4. Codex adds missing tests

## Environment Variables

The MCP server automatically sets:
- `AGENTBOX_INVOCATION_DEPTH=1`: Marks Gemini as being invoked
- `AGENTBOX_CONTAINER_NAME`: Target container name (inherited)

## Error Handling

**Common errors:**
- `"Nested agent invocations not allowed"`: Attempted to invoke from already-invoked agent
- `"Gemini task timed out after X seconds"`: Task exceeded timeout
- `"Gemini invocation failed: <error>"`: General execution error

Errors are returned to the calling agent, not to the user.

## Notes

- This is designed for autonomous (super) agent communication
- For interactive workflows, use the standard `abox gemini` command
- Invocations are logged but do not create desktop notifications by default
- Gemini excels at thorough analysis - use for validation and research tasks
- Multimodal capabilities available (images, etc.) in invoked context
