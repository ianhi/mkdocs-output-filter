# MCP Server

mkdocs-output-filter includes an MCP (Model Context Protocol) server that allows AI code assistants like Claude Code to programmatically access mkdocs build issues.

## Overview

The MCP server provides tools for:

- **`get_issues`** - Get current warnings and errors from the last build
- **`get_issue_details`** - Get detailed information about a specific issue
- **`rebuild`** - Trigger a new mkdocs build and get updated issues
- **`get_build_info`** - Get server URL, build directory, and timing
- **`get_raw_output`** - Get raw mkdocs output for debugging

## Setup

### Watch Mode (Recommended)

Watch mode connects to a running `mkdocs-output-filter` CLI, allowing the MCP server to see real-time build issues as you develop.

**Step 1:** Run mkdocs with the filter and state sharing enabled:

```bash
mkdocs serve 2>&1 | mkdocs-output-filter --share-state
```

**Step 2:** Configure Claude Code to use the MCP server. Add to `.claude/settings.local.json` in your project:

```json
{
  "mcpServers": {
    "mkdocs-output-filter": {
      "command": "mkdocs-output-filter",
      "args": ["mcp", "--watch"]
    }
  }
}
```

The server auto-detects the project from Claude Code's working directory - no need to specify a path!

### Subprocess Mode

For one-off builds where you don't have mkdocs running, the MCP server can run mkdocs itself:

```bash
mkdocs-output-filter mcp --project-dir /path/to/project
```

Or add to Claude Code config:

```json
{
  "mcpServers": {
    "mkdocs-output-filter": {
      "command": "mkdocs-output-filter",
      "args": ["mcp", "--project-dir", "."]
    }
  }
}
```

### Pipe Mode

For advanced use cases, receive mkdocs output via stdin:

```bash
mkdocs build 2>&1 | mkdocs-output-filter mcp --pipe
```

## Tools

### `get_issues`

Get current warnings and errors.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `filter` | `string` | Filter issues: `"all"`, `"errors"`, or `"warnings"` |
| `verbose` | `boolean` | Include full code blocks and tracebacks |

**Returns:** JSON with issue count and array

```json
{
  "total": 2,
  "errors": 0,
  "warnings": 2,
  "issues": [
    {
      "id": "issue-abc123",
      "level": "WARNING",
      "source": "markdown_exec",
      "message": "ValueError: test error",
      "file": "docs/index.md → session 'test' → line 8"
    }
  ]
}
```

### `get_issue_details`

Get detailed information about a specific issue including code and traceback.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `issue_id` | `string` | The issue ID from `get_issues` |

**Returns:** Full issue object with code and traceback

### `rebuild`

Trigger a new mkdocs build (subprocess mode) or refresh from state file (watch mode).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `verbose` | `boolean` | Run mkdocs with verbose flag |

**Returns:** Updated issues list and build info

```json
{
  "success": true,
  "return_code": 0,
  "total_issues": 1,
  "errors": 0,
  "warnings": 1,
  "build_time": "1.23",
  "issues": [...]
}
```

### `get_build_info`

Get information about the last build.

**Returns:**

```json
{
  "server_url": "http://127.0.0.1:8000/",
  "build_dir": "/path/to/site",
  "build_time": "1.23"
}
```

### `get_raw_output`

Get the raw mkdocs output from the last build.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `last_n_lines` | `integer` | Number of lines to return (default: 100) |

## Workflow Example

When working on documentation, AI assistants can:

1. Call `get_issues` to check for current errors
2. Read the relevant file and fix the issue
3. Save the file to trigger a rebuild (in watch mode)
4. Call `get_issues` again to verify the fix

With watch mode, changes are detected automatically - no need to manually call `rebuild`!
