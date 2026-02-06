# MCP Server

docs-output-filter includes an MCP (Model Context Protocol) server that allows AI code assistants like Claude Code to programmatically access documentation build issues.

## Overview

The MCP server provides tools for:

- **`get_issues`** - Get current warnings/errors and INFO message summary from the last build
- **`get_issue_details`** - Get detailed information about a specific issue
- **`get_info`** - Get INFO-level messages (broken links, missing nav entries, deprecation warnings, etc.)
- **`rebuild`** - Trigger a new build and get updated issues
- **`get_build_info`** - Get server URL, build directory, and timing
- **`get_raw_output`** - Get raw build output for debugging
- **`fetch_build_log`** - Fetch and process a remote build log (e.g., ReadTheDocs)

## Setup

### Watch Mode (Recommended)

Watch mode connects to a running `docs-output-filter` CLI, allowing the MCP server to see real-time build issues as you develop.

**Step 1:** Run your build tool with the filter and state sharing enabled:

```bash
# MkDocs
docs-output-filter --share-state -- mkdocs serve --livereload

# Sphinx
docs-output-filter --share-state -- sphinx-autobuild docs _build/html
```

**Step 2:** Configure Claude Code to use the MCP server:

```bash
# If installed with uv tool install
claude mcp add --scope user --transport stdio docs-output-filter -- docs-output-filter --mcp --watch

# If using uvx (no install needed)
claude mcp add --scope user --transport stdio docs-output-filter -- uvx docs-output-filter --mcp --watch
```

The server auto-detects the project from Claude Code's working directory - no need to specify a path!

### Subprocess Mode

For one-off builds where you don't have a build tool running, the MCP server can run it itself. It auto-detects the project type from `mkdocs.yml` or `conf.py`:

```bash
claude mcp add --transport stdio docs-output-filter -- docs-output-filter --mcp --project-dir .
```

### Pipe Mode

For advanced use cases, receive build output via stdin:

```bash
mkdocs build 2>&1 | docs-output-filter --mcp --pipe
sphinx-build docs _build 2>&1 | docs-output-filter --mcp --pipe
```

## Tools

### `get_issues`

Get current warnings and errors. Also includes a summary of INFO-level messages (broken links, missing nav, etc.) so you know to check `get_info` for details.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `filter` | `string` | Filter issues: `"all"`, `"errors"`, or `"warnings"` |
| `verbose` | `boolean` | Include full code blocks and tracebacks |

**Returns:** JSON with issue count, array, and INFO message summary

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
  ],
  "info_summary": {
    "broken_link": 5,
    "missing_nav": 2,
    "total": 7
  }
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

Trigger a new build (subprocess mode) or refresh from state file (watch mode).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `verbose` | `boolean` | Run build tool with verbose flag |

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

Get the raw build output from the last build.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `last_n_lines` | `integer` | Number of lines to return (default: 100) |

### `get_info`

Get INFO-level messages like broken links, missing nav entries, and deprecation warnings.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `category` | `string` | Filter by category: `"all"`, `"broken_link"`, `"absolute_link"`, `"unrecognized_link"`, `"missing_nav"`, `"no_git_logs"`, `"deprecation_warning"` |
| `grouped` | `boolean` | Group messages by category (default: true) |

**Returns:**

```json
{
  "info_messages": {
    "broken_link": [
      {"file": "docs/guide.md", "target": "missing.md", "suggestion": null}
    ],
    "deprecation_warning": [
      {"file": "sphinx_rtd_theme", "target": "RemovedInSphinx80Warning", "suggestion": "The deprecated 'app' argument is removed in Sphinx 8"}
    ]
  },
  "count": 2
}
```

### `fetch_build_log`

Fetch and process a remote build log from a URL (e.g., ReadTheDocs CI builds).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `url` | `string` | URL of the build log to fetch (required) |
| `verbose` | `boolean` | Include full code blocks and tracebacks |

**Returns:**

```json
{
  "url": "https://readthedocs.org/api/v3/projects/myproject/builds/123/",
  "lines_processed": 500,
  "total_issues": 2,
  "errors": 1,
  "warnings": 1,
  "issues": [...],
  "info_messages": {...},
  "build_time": "45.2"
}
```

**Supported URL formats:**

- **ReadTheDocs** - paste the web UI URL directly (e.g., `https://app.readthedocs.org/projects/foo/builds/123/`)
- Plain text log files
- JSON with common log fields (`output`, `log`, `build_log`, `stdout`, `stderr`)

## Workflow Example

When working on documentation, AI assistants can:

1. Call `get_issues` to check for current errors
2. Read the relevant file and fix the issue
3. Save the file to trigger a rebuild (in watch mode)
4. Call `get_issues` again to verify the fix

With watch mode, changes are detected automatically - no need to manually call `rebuild`!
