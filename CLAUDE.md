# docs-output-filter Development Guide

## Project Overview

`docs-output-filter` is a CLI tool that filters documentation build output (MkDocs, Sphinx) to show only warnings and errors with nice formatting.

```bash
# Wrapper mode (recommended) — runs the command, no 2>&1 needed
docs-output-filter -- mkdocs serve --livereload
docs-output-filter -- sphinx-autobuild docs docs/_build/html
docs-output-filter -v -- mkdocs build --verbose

# Pipe mode — traditional Unix pipe
mkdocs build 2>&1 | docs-output-filter
sphinx-build docs _build 2>&1 | docs-output-filter
```

Auto-detects the build tool from output. Use `--tool mkdocs|sphinx` to force.
Streaming mode is now the default (use `--batch` to force batch mode).

## Key Features

- **Wrapper mode** (`--`) - runs build command as subprocess, no `2>&1` needed, fixes buffering issues
- **Multi-tool support** - MkDocs and Sphinx via backend/strategy pattern with auto-detection
- **Streaming mode** (default) - processes output incrementally, shows errors after each build/rebuild
- **Progress spinner** during build with current activity and server URL shown
- **Filtered output** showing only WARNING and ERROR level messages
- **INFO message grouping** - broken links, missing nav items, absolute links, deprecation warnings
- **Code block display** for markdown_exec and myst-nb CellExecutionError with syntax highlighting
- **Error output panel** showing the actual exception (condensed by default, full with `-v`)
- **Location info** including file, line number, session name, and warning codes
- **Sphinx crash handling** - detects "Sphinx exited with exit code: N" as build boundary
- **Warning count mismatch detection** - warns when build reports more warnings than captured
- **Build info** (output dir, server URL, build time)
- **Interactive mode** (`-i`) for toggling between filtered/raw output during serve
- **Remote log fetching** (`--url`) - fetch and parse build logs from ReadTheDocs or any URL
- **State sharing** (`--share-state`) - writes state file for MCP server integration
- **MCP server** (`--mcp`) for code agent integration with 7 tools

## Commands

```bash
# Install globally for testing
uv tool install --force -e .

# Run tests
uv run pytest

# Run specific test
uv run pytest tests/test_cli.py -k "streaming" -v

# Linting
uv run ruff check .
uv run ruff format .

# Build package
uv build
```

## Manual Testing

**IMPORTANT:** Due to a [Click 8.3.x bug](https://github.com/mkdocs/mkdocs/issues/4032), you must use `--livereload` flag for file watching to work:

```bash
mkdocs serve --livereload 2>&1 | docs-output-filter --streaming
```

Test with the markdown_exec error fixture:

```bash
# Test streaming mode with mkdocs serve (recommended)
cd tests/fixtures/markdown_exec_error && mkdocs serve --livereload 2>&1 | docs-output-filter --streaming

# Test batch mode with mkdocs build
cd tests/fixtures/markdown_exec_error && mkdocs build 2>&1 | docs-output-filter

# Test verbose mode (shows full traceback)
cd tests/fixtures/markdown_exec_error && mkdocs build 2>&1 | docs-output-filter -v

# Test interactive mode
cd tests/fixtures/markdown_exec_error && mkdocs serve 2>&1 | docs-output-filter -i
```

When testing streaming mode with `mkdocs serve`:
1. The initial build error should appear immediately
2. Edit `docs/index.md` and save to trigger a rebuild
3. The error should appear again after the rebuild completes

## Architecture

### File Structure

```
src/docs_output_filter/
├── __init__.py      # Package facade: re-exports all public symbols for backward compat
├── __main__.py      # Entry point for `python -m docs_output_filter`
├── cli.py           # main() argparse, mode dispatch, __version__
├── display.py       # Rich formatting: print_issue, print_info_groups, print_summary
├── processor.py     # StreamingProcessor: incremental parser with buffer management
├── modes.py         # Run modes: streaming, batch, interactive, URL, wrap
├── remote.py        # Remote log fetching: ReadTheDocs URL transform, fetch_remote_log
├── types.py         # Shared types: Level, Issue, BuildInfo, InfoCategory, ChunkBoundary
├── state.py         # State file I/O: find_project_root, read/write state, serialization
├── backends/
│   ├── __init__.py  # Backend protocol, BuildTool enum, registry, auto-detect
│   ├── mkdocs.py    # MkDocs backend: parse warnings/errors, markdown_exec, info messages
│   └── sphinx.py    # Sphinx backend: parse warnings/errors, deprecation grouping
└── mcp_server.py    # MCP server for code agent integration (7 tools)
```

### Backend Pattern

Each build tool has a `Backend` class implementing:
- `detect(line)` - identify output from this tool
- `parse_issues(lines)` - extract warnings/errors
- `parse_info_messages(lines)` - extract INFO-level messages
- `detect_chunk_boundary(line, prev_line)` - streaming chunk detection
- `extract_build_info(lines)` - extract server URL, build dir, timing
- `is_in_multiline_block(lines)` - check for unclosed multi-line blocks

Auto-detection tries each backend's `detect()` on incoming lines.

### Key Classes

**In types.py:**
- `Level`: Enum for ERROR/WARNING
- `Issue`: Dataclass (level, source, message, file, code, output, line_number, warning_code)
- `BuildInfo`: Dataclass for server URL, build dir, build time
- `InfoCategory`: Enum for BROKEN_LINK, ABSOLUTE_LINK, MISSING_NAV, DEPRECATION_WARNING, etc.
- `InfoMessage`: Dataclass for INFO-level messages
- `ChunkBoundary`: Enum for BUILD_COMPLETE, SERVER_STARTED, REBUILD_STARTED

**In state.py:**
- `StateFileData`: Dataclass for state file shared between CLI and MCP server
- State stored in temp dir (`/tmp/docs-output-filter/<hash>/state.json`), not in project

**In processor.py:**
- `StreamingProcessor`: Backend-aware stateful processor for incremental parsing

**In display.py:**
- `DisplayMode`: Enum for interactive mode (FILTERED/RAW)

### Streaming Mode

Streaming is the default mode. Uses `sys.stdin.readline()` (not `for line in sys.stdin`) to avoid potential read-ahead buffering issues. Output is displayed when chunk boundaries are detected:
- `BUILD_COMPLETE`: "Documentation built in X seconds" (MkDocs) or "build succeeded" (Sphinx)
- `SERVER_STARTED`: "Serving on http://..."
- `REBUILD_STARTED`: "Detected file changes" / "[sphinx-autobuild] Detected change"

## Test Fixtures

```
tests/fixtures/
├── basic_site/              # Clean MkDocs site with no errors
├── markdown_exec_error/     # MkDocs site with intentional ValueError in code block
├── broken_links/            # MkDocs site with broken internal links
├── multiple_errors/         # MkDocs site with various error types
├── sphinx_warnings/         # Sphinx output with warnings and deprecation warnings
├── sphinx_errors/           # Sphinx output with errors
├── sphinx_autobuild/        # sphinx-autobuild output with rebuild detection
└── sphinx_jupyter_book/     # Real Jupyter Book / myst-nb / sphinx-autobuild output
```

## CLI Flags

**Mode selection:**
- `-- COMMAND`: Wrapper mode - run command as subprocess (recommended, no `2>&1` needed)
- `--streaming`: Force streaming mode (default when not `--batch`)
- `--batch`: Force batch mode (wait for all input before processing)
- `-i, --interactive`: Interactive mode - toggle between filtered/raw with keyboard
- `--raw`: Pass through raw output without filtering
- `--url URL`: Fetch and process a remote build log (e.g., ReadTheDocs)
- `--mcp`: Run as MCP server (use with `--watch`, `--project-dir`, or `--pipe`)
- `--tool mkdocs|sphinx|auto`: Specify build tool (default: auto-detect)

**Display options:**
- `-v, --verbose`: Show full code blocks and tracebacks
- `-e, --errors-only`: Show only errors, not warnings
- `--no-progress`: Disable progress spinner
- `--no-color`: Disable colored output

**State sharing (for MCP integration):**
- `--share-state`: Write state file for MCP server (stored in temp directory, nothing in project)

**MCP server options (require `--mcp`):**
- `--project-dir DIR`: Project directory for subprocess mode
- `--watch`: Watch mode - read state file written by CLI with `--share-state`
- `--pipe`: Pipe mode - read build output from stdin

## MCP Server

For code agent integration. Preferred invocation is via the `--mcp` flag on the main CLI:

```bash
# Watch mode (recommended) - reads state from CLI running with --share-state
docs-output-filter --mcp --watch

# Subprocess mode - manages builds internally (detects mkdocs.yml or conf.py)
docs-output-filter --mcp --project-dir /path/to/project

# Pipe mode - receives build output via stdin
mkdocs build 2>&1 | docs-output-filter --mcp --pipe
```

Tools provided (7 total):
- `get_issues`: Get current warnings/errors (filterable: all, errors, warnings)
- `get_issue_details`: Get full details for a specific issue by ID
- `rebuild`: Trigger a new build and return updated issues
- `get_build_info`: Get server URL, build directory, build time
- `get_raw_output`: Get last N lines of raw build output
- `get_info`: Get INFO-level messages (broken links, absolute links, missing nav, deprecation warnings)
- `fetch_build_log`: Fetch and parse remote build logs (supports ReadTheDocs URL auto-transform)

## File Organization

- **Prefer many small, focused files** over large monolithic ones
- **Every source file must have a clear module-level docstring** explaining:
  - What the file contains and its responsibility
  - Key classes/functions defined in it
  - When to update this docstring (e.g., "Update this description if you add new display functions or change the rendering approach")
- Docstrings should be written for AI agent consumption — be specific about what's in the file so agents can decide whether to read it without opening every file
- When making significant changes to a file (adding new public functions, changing its responsibility), update its docstring to match

## Git Workflow

- **Commit regularly** — make small, focused commits as you complete each logical unit of work
- Avoid accumulating large changesets; a clear commit history is more valuable than fewer commits
- Group related changes (e.g., "add feature X" separate from "update docs for feature X")
- Run tests before committing to keep the history bisectable

## Notes for Development

- Always test with real build output using the fixtures
- The spinner uses Rich's Live display - `transient=True` prevents output interference
- Use `--no-progress` flag when debugging to see cleaner output
- Parsing must handle multi-line blocks (markdown_exec errors span many lines)
- Use `mkdocs build --verbose` to get file paths in error output
- State file is stored in a temp directory keyed by project path hash (no files in project dir)
- State is written atomically (temp file + rename) and shared between CLI and MCP server
- Project root detection checks for `mkdocs.yml` or `conf.py`
- MCP server auto-detects project type and uses appropriate build command

## Keeping CLAUDE.md Up to Date

When making changes to this project, update this file to reflect:
- **New CLI flags or options** - add to the CLI Flags section
- **New MCP tools** - add to the MCP Server tools list
- **New source files or major classes** - update the Architecture/File Structure section
- **New test fixtures** - add to the Test Fixtures section
- **Changed defaults or behavior** - update relevant sections
- **New features** - add to the Key Features list
- **New backends** - add to the Backend Pattern section

Keep descriptions concise. This file is loaded into AI agent context, so brevity matters.
