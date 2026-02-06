# docs-output-filter

```bash
docs-output-filter -- mkdocs serve --livereload
```

**Filter documentation build output to show only what matters: warnings and errors.**

Works with **MkDocs** and **Sphinx** (including sphinx-autobuild, Jupyter Book, myst-nb). Includes an [MCP server](mcp-server.md) for AI code assistant integration (Claude Code, etc.).

## What It Does

docs-output-filter processes documentation build output and:

- **Shows** WARNING and ERROR level messages with rich formatting
- **Highlights** code execution errors (markdown_exec, myst-nb) with syntax-highlighted code blocks
- **Extracts** file locations, line numbers, session names, and warning codes
- **Hides** routine INFO messages (building, cleaning, copying assets)
- **Auto-detects** MkDocs vs Sphinx from the output

## Before & After

<div class="comparison">
<div class="comparison-item">
<div class="comparison-header bad">Raw mkdocs output</div>

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
INFO    -  Reading page 'api/models.md'
INFO    -  Copying static files from theme: material
INFO    -  Copying 'assets/stylesheets/extra.css'
INFO    -  Copying 'assets/javascripts/extra.js'
[git-revision-date-localized-plugin] 'docs/new-page.md' has no git logs
[git-revision-date-localized-plugin] 'docs/draft.md' has no git logs
[git-revision-date-localized-plugin] 'docs/changelog.md' has no git logs
INFO    -  Executing code blocks with markdown_exec...
WARNING -  markdown_exec: Execution of python code block exited with errors

Code block is:

  import numpy as np
  data = np.random.rand(10, 10)
  raise ValueError("INTENTIONAL TEST ERROR")

Output is:

  Traceback (most recent call last):
    File "<code block: session test; n1>", line 3, in <module>
      raise ValueError("INTENTIONAL TEST ERROR")
  ValueError: INTENTIONAL TEST ERROR

WARNING -  [git-revision-date-localized-plugin] Unable to read git logs
INFO    -  Rendering 'index.md'
INFO    -  Rendering 'guide/getting-started.md'
INFO    -  Rendering 'guide/configuration.md'
INFO    -  Rendering 'api/reference.md'
INFO    -  Rendering 'api/models.md'
INFO    -  Building search index...
INFO    -  Writing 'sitemap.xml'
INFO    -  Writing 'search/search_index.json'
INFO    -  Documentation built in 12.34 seconds
```

</div>
<div class="comparison-item">
<div class="comparison-header good">Filtered output</div>

<div class="terminal">
<span class="yellow">⚠ WARNING</span> <span class="dim">[markdown_exec]</span> ValueError: INTENTIONAL TEST ERROR
<span class="cyan">   📍 session </span><span class="green">'test'</span><span class="cyan"> → line </span><span class="cyan-bold">3</span>

<span class="cyan">╭─────────────────── Code Block ───────────────────╮</span>
<span class="cyan">│</span><span class="code-bg">   <span class="line-num">1</span> <span class="keyword">import</span> numpy <span class="keyword">as</span> np                          </span><span class="cyan">│</span>
<span class="cyan">│</span><span class="code-bg">   <span class="line-num">2</span> data = np.random.rand(<span class="number">10</span>, <span class="number">10</span>)                </span><span class="cyan">│</span>
<span class="cyan">│</span><span class="code-bg">   <span class="line-num">3</span> <span class="keyword">raise</span> <span class="exception">ValueError</span>(<span class="string">"INTENTIONAL TEST ERROR"</span>)  </span><span class="cyan">│</span>
<span class="cyan">╰──────────────────────────────────────────────────╯</span>
<span class="red">╭────────────── Error Output ──────────────╮</span>
<span class="red">│</span> ValueError: INTENTIONAL TEST ERROR       <span class="red">│</span>
<span class="red">╰────────── use -v for full traceback ─────╯</span>

<span class="yellow">⚠ WARNING</span> <span class="dim">[git-revision]</span> Unable to read git logs

<span class="dim">────────────────────────────────────────</span>
Summary: <span class="yellow">2 warning(s)</span>

<span class="green-bold">🌐 Server:</span> http://127.0.0.1:8000/
<span class="blue-bold">📁 Output:</span> /project/site
<span class="dim">Built in </span><span class="cyan">12.34</span><span class="dim">s</span>
</div>

</div>
</div>

## Install

```bash
# Run directly (no install needed)
uvx docs-output-filter -- mkdocs build

# Or install permanently
uv tool install docs-output-filter

# Or with pip
pip install docs-output-filter
```

## Quick Start

```bash
# Wrapper mode (recommended) — just prefix your build command
docs-output-filter -- mkdocs build
docs-output-filter -- mkdocs serve --livereload
docs-output-filter -- sphinx-autobuild docs _build/html

# Pipe mode — traditional Unix pipe
mkdocs build 2>&1 | docs-output-filter
sphinx-build docs _build 2>&1 | docs-output-filter
```

!!! tip "Wrapper mode is the easiest way"
    Wrapper mode (`--`) runs the command for you, automatically captures both stdout and stderr, and fixes buffering issues with sphinx-autobuild. No `2>&1` needed.

!!! note "Why `2>&1` in pipe mode?"
    Sphinx writes warnings and errors to stderr. Without `2>&1`, they bypass the filter entirely. MkDocs writes everything to stdout, so `2>&1` is optional but harmless. Wrapper mode handles this automatically.

## Features

| Feature | Description |
|---------|-------------|
| **Multi-tool support** | MkDocs and Sphinx with auto-detection |
| **Filtered output** | Shows WARNING and ERROR messages, hides routine INFO |
| **Code blocks** | Syntax-highlighted code that caused markdown_exec or myst-nb errors |
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

See [Usage](usage.md) for more details and [MCP Server](mcp-server.md) for AI assistant integration.
