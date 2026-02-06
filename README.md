# docs-output-filter

**Filter documentation build output to show only what matters: warnings and errors.**

Works with **MkDocs** and **Sphinx** (including sphinx-autobuild, Jupyter Book, myst-nb). Includes an MCP server for AI code assistant integration (Claude Code, etc.).

## Before & After

<table>
<tr>
<th>❌ Raw build output (43 lines)</th>
<th>✅ Filtered output (15 lines)</th>
</tr>
<tr>
<td>

```
INFO    -  Building documentation...
INFO    -  Cleaning site directory
INFO    -  Log level set to INFO
INFO    -  Building documentation to directory: /project/site
INFO    -  MERMAID2  - Initialization arguments: {}
INFO    -  Generating index pages...
INFO    -  Reading page 'index.md'
INFO    -  Reading page 'guide/getting-started.md'
INFO    -  Reading page 'guide/configuration.md'
INFO    -  Reading page 'api/reference.md'
INFO    -  Copying static files from theme: material
INFO    -  Copying 'assets/stylesheets/extra.css'
INFO    -  Copying 'assets/javascripts/extra.js'
[git-revision-date-localized-plugin] has no git logs
INFO    -  Executing code blocks with markdown_exec...
WARNING -  markdown_exec: Execution of python
code block exited with errors

Code block is:

  import numpy as np
  data = np.random.rand(10, 10)
  raise ValueError("INTENTIONAL TEST ERROR")

Output is:

  Traceback (most recent call last):
    File "<code block: session test; n1>", line 3
      raise ValueError("INTENTIONAL TEST ERROR")
  ValueError: INTENTIONAL TEST ERROR

WARNING -  [git-revision] Unable to read git logs
INFO    -  Rendering 'index.md'
INFO    -  Rendering 'guide/getting-started.md'
INFO    -  Rendering 'guide/configuration.md'
INFO    -  Rendering 'api/reference.md'
INFO    -  Building search index...
INFO    -  Writing 'sitemap.xml'
INFO    -  Writing 'search/search_index.json'
INFO    -  Documentation built in 12.34 seconds
```

</td>
<td>

```
⚠ WARNING [markdown_exec] ValueError: INTENTIONAL TEST ERROR
   📍 session 'test' → line 3

╭──────────────── Code Block ─────────────────╮
│  1 import numpy as np                       │
│  2 data = np.random.rand(10, 10)            │
│  3 raise ValueError("INTENTIONAL TEST E...")│
╰─────────────────────────────────────────────╯
╭─────────── Error Output ────────────╮
│ ValueError: INTENTIONAL TEST ERROR  │
╰───────── use -v for full traceback ─╯

⚠ WARNING [git-revision] Unable to read git logs

Summary: 2 warning(s)

🌐 Server: http://127.0.0.1:8000/
📁 Output: /project/site
Built in 12.34s
```

</td>
</tr>
</table>

## Installation

```bash
# Run directly (no install needed)
uvx docs-output-filter -- mkdocs build

# Or install permanently
uv tool install docs-output-filter

# Or with pip
pip install docs-output-filter
```

## Usage

```bash
# Wrapper mode (recommended) — just prefix your build command
docs-output-filter -- mkdocs build
docs-output-filter -- mkdocs serve --livereload
docs-output-filter -- sphinx-autobuild docs _build/html

# Pipe mode — traditional Unix pipe
mkdocs build 2>&1 | docs-output-filter
sphinx-build docs _build 2>&1 | docs-output-filter

# Process a remote build log (e.g., ReadTheDocs)
docs-output-filter --url https://app.readthedocs.org/projects/myproject/builds/12345/
```

> **Tip:** Wrapper mode (`--`) is the easiest way to use docs-output-filter. It runs the command for you, automatically captures both stdout and stderr, and fixes buffering issues with sphinx-autobuild. No `2>&1` needed.

> **Note:** If using pipe mode, `2>&1` is important — Sphinx writes warnings to stderr. Without it, warnings bypass the filter.

> **Note:** Use `--livereload` with `mkdocs serve` due to a [Click 8.3.x bug](https://github.com/mkdocs/mkdocs/issues/4032).

## Features

| Feature | Description |
|---------|-------------|
| **Multi-tool support** | MkDocs and Sphinx with auto-detection |
| **Filtered output** | Shows WARNING and ERROR messages, hides routine INFO |
| **Code blocks** | Syntax-highlighted code for markdown_exec and myst-nb errors |
| **Location info** | File, line number, session name, warning codes |
| **Streaming mode** | Real-time output for `mkdocs serve` / `sphinx-autobuild` with rebuild detection |
| **Interactive mode** | Toggle between raw/filtered with keyboard (`-i`) |
| **Remote logs** | Fetch and parse build logs from ReadTheDocs and other CI |
| **MCP server** | API for AI code assistants like Claude Code |

## Options

| Flag | Description |
|------|-------------|
| `-- COMMAND` | Run command as subprocess (recommended, no `2>&1` needed) |
| `-v, --verbose` | Show full tracebacks and code blocks |
| `-e, --errors-only` | Hide warnings, show only errors |
| `--no-color` | Disable colored output |
| `--raw` | Pass through unfiltered build output |
| `-i, --interactive` | Toggle raw/filtered with keyboard |
| `--url URL` | Fetch and process a remote build log |
| `--tool mkdocs\|sphinx\|auto` | Force build tool detection (default: auto) |
| `--share-state` | Write state for MCP server integration |

## MCP Server (for AI Assistants)

Enable AI code assistants to access build issues:

```bash
# Terminal 1: Run build tool with state sharing
docs-output-filter --share-state -- mkdocs serve --livereload

# Terminal 2: Add MCP server to Claude Code (if installed)
claude mcp add --scope user --transport stdio docs-output-filter -- docs-output-filter --mcp --watch
# Or with uvx (no install needed)
claude mcp add --scope user --transport stdio docs-output-filter -- uvx docs-output-filter --mcp --watch
```

## Documentation

Full documentation: https://ianhuntisaak.com/docs-output-filter/

## Development

```bash
git clone https://github.com/ianhi/docs-output-filter
cd docs-output-filter
uv sync
uv run pre-commit install
uv run pytest
```

## License

MIT
