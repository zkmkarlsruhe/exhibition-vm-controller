# SSH MCP Server

Manage and control remote servers via SSH through the Model Context Protocol.

## Features

- **Auto-discovery**: Automatically reads SSH hosts from `~/.ssh/config` and `~/.ssh/known_hosts`
- **Native SSH tools**: Uses native `ssh` and `scp` commands for maximum reliability
- **Full SSH support**: Works with SSH agents, identity files, jump hosts, and all SSH config features
- **7 core tools**:
  - List available SSH hosts
  - Execute remote commands
  - Get host information
  - Test connectivity
  - Upload/download files via SCP
  - Run batch commands
  - Comprehensive error handling

## Installation

```bash
abox mcp add ssh
```

## Configuration

The server automatically discovers hosts from your SSH configuration files:
- `~/.ssh/config`
- `~/.ssh/known_hosts`

No additional configuration required! The server respects all SSH settings including:
- Custom ports
- Multiple identity files
- Jump hosts (ProxyJump)
- SSH Include directives
- IPv6 addresses

## Usage Examples

Once enabled, you can:
- Execute commands on remote servers
- Transfer files between local and remote systems
- Check connectivity to SSH hosts
- Run batch operations across multiple servers
- Query SSH host configurations

## Notes

- This MCP uses native SSH tools rather than JavaScript SSH libraries for better reliability
- All SSH authentication methods (keys, agents, passwords) are supported
- The server automatically handles SSH connection pooling and error recovery

## Package

NPM Package: `@aiondadotcom/mcp-ssh`
Repository: https://github.com/AiondaDotCom/mcp-ssh
