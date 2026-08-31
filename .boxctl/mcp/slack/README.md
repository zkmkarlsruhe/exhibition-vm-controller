# Slack MCP Server

Powerful Slack workspace integration for channels, messages, and users.

## Installation

```bash
abox mcp add slack
```

## Configuration

1. Create a Slack app at https://api.slack.com/apps
2. Add required bot token scopes: `channels:read`, `chat:write`, `users:read`
3. Install the app to your workspace
4. Copy the bot token (starts with `xoxb-`)
5. Set environment variables or update the config

## Usage

- Read channel messages
- Send messages to channels
- List workspace users
- Search conversations
