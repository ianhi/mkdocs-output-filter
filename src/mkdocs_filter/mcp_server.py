"""MCP Server for mkdocs-output-filter.

Provides tools for code agents to get mkdocs issues and collaborate on fixes.

Usage:
    # Watch mode (recommended): Read state from running mkdocs-output-filter CLI
    mkdocs-output-filter mcp --watch

    # Subprocess mode: Server manages mkdocs internally
    mkdocs-output-filter mcp --project-dir /path/to/project

    # Pipe mode: Receive mkdocs output via stdin
    mkdocs build 2>&1 | mkdocs-output-filter mcp --pipe
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mkdocs_filter.parsing import (
    BuildInfo,
    InfoCategory,
    InfoMessage,
    Issue,
    Level,
    extract_build_info,
    group_info_messages,
    parse_mkdocs_output,
    read_state_file,
)


class MkdocsFilterServer:
    """MCP server for mkdocs-filter.

    Provides tools for code agents to interact with mkdocs build issues.
    """

    def __init__(
        self,
        project_dir: Path | None = None,
        pipe_mode: bool = False,
        watch_mode: bool = False,
    ):
        """Initialize the server.

        Args:
            project_dir: Path to mkdocs project directory (for subprocess mode)
            pipe_mode: If True, expect mkdocs output from stdin
            watch_mode: If True, read state from state file written by CLI
        """
        self.project_dir = project_dir
        self.pipe_mode = pipe_mode
        self.watch_mode = watch_mode
        self.issues: list[Issue] = []
        self.info_messages: list[InfoMessage] = []
        self.build_info = BuildInfo()
        self.raw_output: list[str] = []
        self._issue_ids: dict[str, str] = {}  # Cache for stable issue IDs
        self._last_state_timestamp: float = 0

        # Create MCP server
        self._server = Server("mkdocs-filter")
        self._setup_tools()

    def _setup_tools(self) -> None:
        """Set up MCP tool handlers."""

        @self._server.list_tools()
        async def list_tools() -> list[Tool]:
            return self._list_tools()

        @self._server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            return self._call_tool(name, arguments)

    def _list_tools(self) -> list[Tool]:
        """List available tools."""
        return [
            Tool(
                name="get_issues",
                description="Get current warnings and errors from the last mkdocs build",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "enum": ["all", "errors", "warnings"],
                            "default": "all",
                            "description": "Filter issues by type",
                        },
                        "verbose": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include full code blocks and tracebacks",
                        },
                    },
                },
            ),
            Tool(
                name="get_issue_details",
                description="Get detailed information about a specific issue by ID",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue_id": {
                            "type": "string",
                            "description": "The issue ID to get details for",
                        },
                    },
                    "required": ["issue_id"],
                },
            ),
            Tool(
                name="rebuild",
                description="Trigger a new mkdocs build and return updated issues",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "verbose": {
                            "type": "boolean",
                            "default": False,
                            "description": "Run mkdocs with --verbose for more file context",
                        },
                    },
                },
            ),
            Tool(
                name="get_build_info",
                description="Get information about the last build (server URL, build dir, time)",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="get_raw_output",
                description="Get the raw mkdocs output from the last build",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "last_n_lines": {
                            "type": "integer",
                            "default": 100,
                            "description": "Number of lines to return (from the end)",
                        },
                    },
                },
            ),
            Tool(
                name="get_info",
                description="Get INFO-level messages like broken links, missing nav entries, absolute links",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": [
                                "all",
                                "broken_link",
                                "absolute_link",
                                "unrecognized_link",
                                "missing_nav",
                                "no_git_logs",
                            ],
                            "default": "all",
                            "description": "Filter by category",
                        },
                        "grouped": {
                            "type": "boolean",
                            "default": True,
                            "description": "Group messages by category",
                        },
                    },
                },
            ),
        ]

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle tool calls."""
        if name == "get_issues":
            return self._handle_get_issues(arguments)
        elif name == "get_issue_details":
            return self._handle_get_issue_details(arguments)
        elif name == "rebuild":
            return self._handle_rebuild(arguments)
        elif name == "get_build_info":
            return self._handle_get_build_info()
        elif name == "get_raw_output":
            return self._handle_get_raw_output(arguments)
        elif name == "get_info":
            return self._handle_get_info(arguments)
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    def _refresh_from_state_file(self) -> bool:
        """Refresh issues from state file if in watch mode.

        Returns True if state was refreshed, False otherwise.
        """
        if not self.watch_mode:
            return False

        state = read_state_file(self.project_dir)
        if state is None:
            return False

        # Check if state has been updated since last read
        if state.timestamp <= self._last_state_timestamp:
            return False

        # Update our state from the file
        self._last_state_timestamp = state.timestamp
        self.issues = state.issues
        self.info_messages = state.info_messages
        self.build_info = state.build_info
        self.raw_output = state.raw_output
        return True

    def _handle_get_issues(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle get_issues tool call."""
        # Refresh from state file if in watch mode
        self._refresh_from_state_file()

        filter_type = arguments.get("filter", "all")
        verbose = arguments.get("verbose", False)

        issues = self.issues

        # Filter issues
        if filter_type == "errors":
            issues = [i for i in issues if i.level == Level.ERROR]
        elif filter_type == "warnings":
            issues = [i for i in issues if i.level == Level.WARNING]

        # Convert to dicts
        issue_dicts = [self._issue_to_dict(i, verbose=verbose) for i in issues]

        # Build response
        error_count = sum(1 for i in issues if i.level == Level.ERROR)
        warning_count = sum(1 for i in issues if i.level == Level.WARNING)

        response = {
            "total": len(issue_dicts),
            "errors": error_count,
            "warnings": warning_count,
            "issues": issue_dicts,
        }

        return [TextContent(type="text", text=json.dumps(response, indent=2))]

    def _handle_get_issue_details(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle get_issue_details tool call."""
        # Refresh from state file if in watch mode
        self._refresh_from_state_file()

        issue_id = arguments.get("issue_id", "")

        # Find issue by ID
        for issue in self.issues:
            if self._get_issue_id(issue) == issue_id:
                issue_dict = self._issue_to_dict(issue, verbose=True)
                return [TextContent(type="text", text=json.dumps(issue_dict, indent=2))]

        return [TextContent(type="text", text=f"Issue not found: {issue_id}")]

    def _handle_rebuild(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle rebuild tool call."""
        if self.pipe_mode:
            return [
                TextContent(
                    type="text",
                    text="Error: Cannot rebuild in pipe mode. Run mkdocs manually and pipe output.",
                )
            ]

        if self.watch_mode:
            # In watch mode, just refresh from state file
            # The user should save a file to trigger a rebuild in mkdocs serve
            refreshed = self._refresh_from_state_file()
            if not refreshed:
                return [
                    TextContent(
                        type="text",
                        text="No new build data. Save a file to trigger a rebuild in mkdocs serve, "
                        "or check that mkdocs-output-filter is running with --share-state.",
                    )
                ]
            # Return current issues
            return self._handle_get_issues(arguments)

        if not self.project_dir:
            return [TextContent(type="text", text="Error: No project directory configured")]

        verbose = arguments.get("verbose", False)

        # Run mkdocs build
        lines, return_code = self._run_mkdocs_build(verbose=verbose)

        # Process output
        self._parse_output("\n".join(lines))

        # Build response
        error_count = sum(1 for i in self.issues if i.level == Level.ERROR)
        warning_count = sum(1 for i in self.issues if i.level == Level.WARNING)

        response = {
            "success": return_code == 0 or (return_code == 1 and error_count == 0),
            "return_code": return_code,
            "total_issues": len(self.issues),
            "errors": error_count,
            "warnings": warning_count,
            "build_time": self.build_info.build_time,
            "issues": [self._issue_to_dict(i) for i in self.issues],
        }

        return [TextContent(type="text", text=json.dumps(response, indent=2))]

    def _handle_get_build_info(self) -> list[TextContent]:
        """Handle get_build_info tool call."""
        # Refresh from state file if in watch mode
        refreshed = self._refresh_from_state_file()

        # Include diagnostic info if in watch mode
        if self.watch_mode:
            from mkdocs_filter.parsing import find_project_root, get_state_file_path

            project_root = find_project_root()
            state_path = get_state_file_path(self.project_dir)

            diag = {
                "watch_mode": True,
                "state_file_found": refreshed or self._last_state_timestamp > 0,
                "project_root": str(project_root) if project_root else None,
                "state_file_path": str(state_path) if state_path else None,
                "cwd": str(Path.cwd()),
            }
            if not (refreshed or self._last_state_timestamp > 0):
                diag["hint"] = (
                    "No state file found. Make sure mkdocs-output-filter is running with --share-state flag "
                    "in a directory containing mkdocs.yml"
                )

            build_info = json.loads(self._get_build_info_json())
            build_info["diagnostics"] = diag
            return [TextContent(type="text", text=json.dumps(build_info, indent=2))]

        return [TextContent(type="text", text=self._get_build_info_json())]

    def _handle_get_raw_output(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle get_raw_output tool call."""
        last_n = arguments.get("last_n_lines", 100)
        lines = self.raw_output[-last_n:] if last_n > 0 else self.raw_output
        return [TextContent(type="text", text="\n".join(lines))]

    def _handle_get_info(self, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle get_info tool call for INFO-level messages."""
        # Refresh from state file if in watch mode
        self._refresh_from_state_file()

        category_filter = arguments.get("category", "all")
        grouped = arguments.get("grouped", True)

        messages = self.info_messages

        # Filter by category if specified
        if category_filter != "all":
            try:
                cat = InfoCategory(category_filter)
                messages = [m for m in messages if m.category == cat]
            except ValueError:
                return [TextContent(type="text", text=f"Unknown category: {category_filter}")]

        if not messages:
            return [
                TextContent(
                    type="text", text=json.dumps({"info_messages": [], "count": 0}, indent=2)
                )
            ]

        if grouped:
            # Group by category
            groups = group_info_messages(messages)
            result: dict[str, Any] = {
                "info_messages": {},
                "count": len(messages),
            }
            for cat, msgs in groups.items():
                result["info_messages"][cat.value] = [
                    {
                        "file": m.file,
                        "target": m.target,
                        "suggestion": m.suggestion,
                    }
                    for m in msgs
                ]
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        else:
            # Flat list
            result_list = [
                {
                    "category": m.category.value,
                    "file": m.file,
                    "target": m.target,
                    "suggestion": m.suggestion,
                }
                for m in messages
            ]
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"info_messages": result_list, "count": len(messages)}, indent=2
                    ),
                )
            ]

    def _parse_output(self, output: str) -> None:
        """Parse mkdocs output and extract issues and build info."""
        lines = output.splitlines()
        self.raw_output = lines

        # Parse issues
        all_issues = parse_mkdocs_output(lines)

        # Deduplicate issues
        seen: set[tuple[Level, str]] = set()
        unique_issues: list[Issue] = []
        for issue in all_issues:
            key = (issue.level, issue.message[:100])
            if key not in seen:
                seen.add(key)
                unique_issues.append(issue)

        self.issues = unique_issues

        # Extract build info
        self.build_info = extract_build_info(lines)

    def _run_mkdocs_build(self, verbose: bool = False) -> tuple[list[str], int]:
        """Run mkdocs build and capture output."""
        if not self.project_dir:
            return [], 1

        cmd = ["mkdocs", "build", "--clean"]
        if verbose:
            cmd.append("--verbose")

        result = subprocess.run(
            cmd,
            cwd=self.project_dir,
            capture_output=True,
            text=True,
        )

        # Combine stdout and stderr
        output = result.stdout + result.stderr
        lines = output.splitlines()

        return lines, result.returncode

    def _get_issue_id(self, issue: Issue) -> str:
        """Get a stable ID for an issue."""
        # Create a hash based on issue content
        content = f"{issue.level.value}:{issue.source}:{issue.message}"
        if issue.file:
            content += f":{issue.file}"

        if content not in self._issue_ids:
            # Generate a short hash
            hash_bytes = hashlib.sha256(content.encode()).digest()
            self._issue_ids[content] = hash_bytes[:4].hex()

        return f"issue-{self._issue_ids[content]}"

    def _issue_to_dict(self, issue: Issue, verbose: bool = False) -> dict[str, Any]:
        """Convert an Issue to a JSON-serializable dict."""
        result: dict[str, Any] = {
            "id": self._get_issue_id(issue),
            "level": issue.level.value,
            "source": issue.source,
            "message": issue.message,
        }

        if issue.file:
            result["file"] = issue.file

        if verbose:
            if issue.code:
                result["code"] = issue.code
            if issue.output:
                result["traceback"] = issue.output

        return result

    def _get_build_info_json(self) -> str:
        """Get build info as JSON string."""
        result: dict[str, Any] = {}
        if self.build_info.server_url:
            result["server_url"] = self.build_info.server_url
        if self.build_info.build_dir:
            result["build_dir"] = self.build_info.build_dir
        if self.build_info.build_time:
            result["build_time"] = self.build_info.build_time
        return json.dumps(result, indent=2)

    async def run(self) -> None:
        """Run the MCP server."""
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream, write_stream, self._server.create_initialization_options()
            )


def run_mcp_server(
    project_dir: str | None = None,
    pipe_mode: bool = False,
    watch_mode: bool = False,
    initial_build: bool = False,
    state_dir: str | None = None,
) -> int:
    """Run the MCP server with the given configuration.

    This function can be called from the main CLI when --mcp is used.

    Args:
        project_dir: Path to mkdocs project directory
        pipe_mode: Read mkdocs output from stdin
        watch_mode: Watch state file for updates
        initial_build: Run initial mkdocs build on startup
        state_dir: Directory to search for state file (for watch mode)

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    import asyncio

    # Validate arguments
    mode_count = sum([bool(project_dir and not watch_mode), pipe_mode, watch_mode])
    if mode_count == 0:
        print(
            "Error: Specify one of --watch, --project-dir, or --pipe",
            file=sys.stderr,
        )
        return 1

    if mode_count > 1 and not (watch_mode and project_dir):
        print(
            "Error: Cannot combine --pipe with other modes",
            file=sys.stderr,
        )
        return 1

    # Validate project directory if specified
    project_path = None
    if project_dir:
        project_path = Path(project_dir)
        if not project_path.exists():
            print(f"Error: Project directory does not exist: {project_dir}", file=sys.stderr)
            return 1
        if not (project_path / "mkdocs.yml").exists():
            print(f"Error: No mkdocs.yml found in {project_dir}", file=sys.stderr)
            return 1

    # Create server
    server = MkdocsFilterServer(
        project_dir=project_path,
        pipe_mode=pipe_mode,
        watch_mode=watch_mode,
    )

    # Handle pipe mode - read initial input
    if pipe_mode:
        lines = []
        for line in sys.stdin:
            lines.append(line.rstrip())
        server._parse_output("\n".join(lines))

    # Handle watch mode - do initial read of state file
    elif watch_mode:
        server._refresh_from_state_file()

    # Handle initial build for subprocess mode
    elif initial_build and project_path:
        lines, _ = server._run_mkdocs_build()
        server._parse_output("\n".join(lines))

    # Run the server
    asyncio.run(server.run())

    return 0


def main() -> int:
    """Main entry point for the MCP server CLI."""
    parser = argparse.ArgumentParser(
        description="MCP Server for mkdocs-output-filter - provides tools for code agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Watch mode (recommended) - auto-detects project from current directory
    # First, run mkdocs-output-filter with --share-state in a terminal:
    #   mkdocs serve 2>&1 | mkdocs-output-filter --share-state
    # Then start the MCP server in watch mode:
    mkdocs-output-filter mcp --watch

    # Subprocess mode (for one-off builds)
    mkdocs-output-filter mcp --project-dir /path/to/mkdocs/project

    # Pipe mode
    mkdocs build 2>&1 | mkdocs-output-filter mcp --pipe

Usage with Claude Code:
    Add to ~/.claude.json mcpServers section:
    {
        "mkdocs-output-filter": {
            "command": "mkdocs-output-filter",
            "args": ["mcp", "--watch"]
        }
    }

    The server auto-detects the project from Claude Code's working directory.
    Just run 'mkdocs serve 2>&1 | mkdocs-output-filter --share-state' in your
    project and Claude Code will see the build issues.
        """,
    )
    parser.add_argument(
        "--project-dir",
        type=str,
        help="Path to mkdocs project directory (for subprocess mode or watch mode)",
    )
    parser.add_argument(
        "--pipe",
        action="store_true",
        help="Read mkdocs output from stdin (pipe mode)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch mode: read state from .mkdocs-output-filter/state.json written by CLI",
    )
    parser.add_argument(
        "--initial-build",
        action="store_true",
        help="Run an initial mkdocs build on startup (subprocess mode only)",
    )
    args = parser.parse_args()

    # Validate arguments
    mode_count = sum([bool(args.project_dir and not args.watch), args.pipe, args.watch])
    if mode_count == 0:
        print(
            "Error: Specify one of --watch, --project-dir, or --pipe",
            file=sys.stderr,
        )
        return 1

    if mode_count > 1 and not (args.watch and args.project_dir):
        print(
            "Error: Cannot combine --pipe with other modes",
            file=sys.stderr,
        )
        return 1

    # Validate project directory if specified
    project_path = None
    if args.project_dir:
        project_path = Path(args.project_dir)
        if not project_path.exists():
            print(f"Error: Project directory does not exist: {args.project_dir}", file=sys.stderr)
            return 1
        if not (project_path / "mkdocs.yml").exists():
            print(f"Error: No mkdocs.yml found in {args.project_dir}", file=sys.stderr)
            return 1

    # Create server
    server = MkdocsFilterServer(
        project_dir=project_path,
        pipe_mode=args.pipe,
        watch_mode=args.watch,
    )

    # Handle pipe mode - read initial input
    if args.pipe:
        lines = []
        for line in sys.stdin:
            lines.append(line.rstrip())
        server._parse_output("\n".join(lines))

    # Handle watch mode - do initial read of state file
    elif args.watch:
        server._refresh_from_state_file()

    # Handle initial build for subprocess mode
    elif args.initial_build and project_path:
        lines, _ = server._run_mkdocs_build()
        server._parse_output("\n".join(lines))

    # Run the server
    import asyncio

    asyncio.run(server.run())

    return 0


if __name__ == "__main__":
    sys.exit(main())
