# PostgreSQL MCP Server

Provides Claude with database access to query and modify PostgreSQL databases.

## What it does

- Execute SQL queries
- Inspect database schema
- Create/modify tables
- Analyze data
- Explain queries

## Connection Methods

### Database on Host Machine

Access your host PostgreSQL via Docker's special hostname:

```json
{
  "mcpServers": {
    "postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres"],
      "env": {
        "POSTGRES_CONNECTION_STRING": "postgresql://user:password@host.docker.internal:5432/mydb"
      }
    }
  }
}
```

### Database in Another Docker Container

Use the container name on the Docker network:

```json
{
  "POSTGRES_CONNECTION_STRING": "postgresql://user:password@postgres-container:5432/mydb"
}
```

### Remote Database

Use the remote host:

```json
{
  "POSTGRES_CONNECTION_STRING": "postgresql://user:password@db.example.com:5432/mydb"
}
```

## Environment Variables

- `POSTGRES_CONNECTION_STRING` - Full PostgreSQL connection string

## Security

**⚠️ Important:** Connection strings contain credentials!

Options:
1. Use `.agentbox/.env` or `.agentbox/.env.local`
2. Reference in config via `${POSTGRES_CONNECTION_STRING}`
3. Use read-only database user

Agentbox loads `.agentbox/.env` on container start and reloads it when it changes.

## Example Usage

```bash
# Add to project
abox mcp add postgres

# Edit agentbox.config.json
# Set your connection string

# Claude can now query your database!
```
