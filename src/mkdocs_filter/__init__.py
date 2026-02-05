"""Filter mkdocs build output to show only warnings and errors with nice formatting.

Usage:
    mkdocs build 2>&1 | mkdocs-output-filter
    mkdocs serve 2>&1 | mkdocs-output-filter -v
"""

from __future__ import annotations

import argparse
import os
import re
import select
import sys
import termios
import threading
import tty
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from queue import Empty, Queue

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


class DisplayMode(Enum):
    """Display mode for interactive mode."""

    FILTERED = "filtered"
    RAW = "raw"


# Re-export parsing module symbols for backwards compatibility
from mkdocs_filter.parsing import (  # noqa: E402
    BuildInfo,
    ChunkBoundary,
    InfoCategory,
    InfoMessage,
    Issue,
    Level,
    StateFileData,
    StreamingState,
    dedent_code,
    detect_chunk_boundary,
    extract_build_info,
    find_project_root,
    get_state_file_path,
    group_info_messages,
    is_in_multiline_block,
    parse_info_messages,
    parse_markdown_exec_issue,
    parse_mkdocs_output,
    read_state_file,
    write_state_file,
)

__version__ = "0.1.0"

__all__ = [
    # Data classes
    "Level",
    "Issue",
    "BuildInfo",
    "StreamingState",
    "StateFileData",
    "ChunkBoundary",
    "DisplayMode",
    "InfoCategory",
    "InfoMessage",
    # Parsing functions
    "detect_chunk_boundary",
    "is_in_multiline_block",
    "extract_build_info",
    "parse_mkdocs_output",
    "parse_markdown_exec_issue",
    "parse_info_messages",
    "group_info_messages",
    "dedent_code",
    # State file functions
    "find_project_root",
    "get_state_file_path",
    "read_state_file",
    "write_state_file",
    # Streaming
    "StreamingProcessor",
    # CLI
    "print_issue",
    "print_info_groups",
    "print_summary",
    "main",
]


class StreamingProcessor:
    """Processes mkdocs output incrementally for streaming mode."""

    BUFFER_MAX_SIZE: int = 200  # Keep last N lines for context
    RAW_BUFFER_MAX_SIZE: int = 500  # Keep last N lines for state file

    def __init__(
        self,
        console: Console,
        verbose: bool = False,
        errors_only: bool = False,
        on_issue: Callable[[Issue], None] | None = None,
        write_state: bool = False,
    ):
        self.console = console
        self.verbose = verbose
        self.errors_only = errors_only
        self.on_issue = on_issue or (lambda issue: print_issue(console, issue, verbose))
        self.write_state = write_state

        self.buffer: list[str] = []
        self.raw_buffer: list[str] = []  # All lines for state file
        self.all_issues: list[Issue] = []
        self.all_info_messages: list[InfoMessage] = []  # Important INFO messages
        self.seen_issues: set[tuple[Level, str]] = set()
        self.seen_info: set[tuple[InfoCategory, str, str | None]] = set()  # Dedupe info messages
        self.build_info = BuildInfo()
        self.prev_line: str | None = None
        self._pending_display = False
        self.saw_mkdocs_output = False  # Track if we saw valid mkdocs output
        self.in_serve_mode = False  # Track if mkdocs serve is running
        self.saw_server_error = False  # Track if server crashed (OSError, etc.)
        self.error_lines: list[str] = []  # Capture error output for display

    def process_line(self, line: str) -> None:
        """Process a single line of mkdocs output."""
        line = line.rstrip()
        self.buffer.append(line)
        self.raw_buffer.append(line)

        # Detect if this looks like mkdocs output
        if not self.saw_mkdocs_output:
            if re.match(r"^(INFO|WARNING|ERROR|DEBUG)\s+-", line) or re.match(
                r"^\d{4}-\d{2}-\d{2}.*?(INFO|WARNING|ERROR)", line
            ):
                self.saw_mkdocs_output = True

        # Detect serve mode
        if "Serving on http" in line:
            self.in_serve_mode = True

        # Detect server errors (OSError, etc.) - only specific system errors
        # NOT generic errors that might be part of mkdocs output
        stripped = line.strip()
        if (
            re.match(r"^OSError:", stripped)
            or re.match(r"^IOError:", stripped)
            or re.match(r"^PermissionError:", stripped)
            or re.match(r"^ConnectionError:", stripped)
            or "Address already in use" in line
            or "Permission denied" in line
            and "OSError" in line
        ):
            self.saw_server_error = True
        # Capture error context once we're in error mode
        if self.saw_server_error:
            self.error_lines.append(line)

        # Keep buffers from growing too large
        if len(self.buffer) > self.BUFFER_MAX_SIZE:
            self.buffer = self.buffer[-self.BUFFER_MAX_SIZE :]
        if len(self.raw_buffer) > self.RAW_BUFFER_MAX_SIZE:
            self.raw_buffer = self.raw_buffer[-self.RAW_BUFFER_MAX_SIZE :]

        # Detect chunk boundaries
        boundary = detect_chunk_boundary(line, self.prev_line)
        self.prev_line = line

        # On rebuild start, clear state
        if boundary == ChunkBoundary.REBUILD_STARTED:
            self._handle_rebuild_start()
            return

        # On build complete or server started, process any pending content
        if boundary in (ChunkBoundary.BUILD_COMPLETE, ChunkBoundary.SERVER_STARTED):
            self._process_buffer()
            self._update_build_info_from_line(line)
            self._write_state_file()
            return

        # Check if we just completed an error block
        if boundary == ChunkBoundary.ERROR_BLOCK_END:
            self._process_buffer()
            return

    def _handle_rebuild_start(self) -> None:
        """Handle the start of a rebuild (file change detected)."""
        # Display any pending issues from previous build
        self._process_buffer()
        # Show rebuild indicator
        self.console.print()
        self.console.print("[dim]─── File change detected, rebuilding... ───[/dim]")
        self.console.print()
        # Clear state for new build
        self.buffer.clear()
        self.raw_buffer.clear()
        self.all_issues.clear()
        self.all_info_messages.clear()
        self.seen_issues.clear()
        self.seen_info.clear()
        self.build_info = BuildInfo()

    def _write_state_file(self) -> None:
        """Write current state to the state file for MCP server access."""
        if not self.write_state:
            return

        state = StateFileData(
            issues=self.all_issues,
            build_info=self.build_info,
            raw_output=self.raw_buffer,
        )
        write_state_file(state)

    def _process_buffer(self) -> None:
        """Process accumulated buffer and display any new issues."""
        if not self.buffer:
            return

        # Update build info
        self._update_build_info(self.buffer)

        # Parse issues from buffer
        issues = parse_mkdocs_output(self.buffer)

        # Parse important INFO messages and dedupe
        info_messages = parse_info_messages(self.buffer)
        for msg in info_messages:
            info_key = (msg.category, msg.file, msg.target)
            if info_key not in self.seen_info:
                self.seen_info.add(info_key)
                self.all_info_messages.append(msg)

        # Filter and dedupe
        for issue in issues:
            if self.errors_only and issue.level != Level.ERROR:
                continue

            issue_key = (issue.level, issue.message[:100])
            if issue_key in self.seen_issues:
                continue

            self.seen_issues.add(issue_key)
            self.all_issues.append(issue)
            self.on_issue(issue)

    def _update_build_info(self, lines: list[str]) -> None:
        """Update build info from lines."""
        info = extract_build_info(lines)
        if info.server_url:
            self.build_info.server_url = info.server_url
        if info.build_dir:
            self.build_info.build_dir = info.build_dir
        if info.build_time:
            self.build_info.build_time = info.build_time

    def _update_build_info_from_line(self, line: str) -> None:
        """Update build info from a single line."""
        self._update_build_info([line])

    def finalize(self) -> tuple[list[Issue], BuildInfo]:
        """Finalize processing and return all issues and build info."""
        # Process any remaining buffer content
        self._process_buffer()
        return self.all_issues, self.build_info


def print_issue(console: Console, issue: Issue, verbose: bool = False) -> None:
    """Print an issue with rich formatting."""
    style = "red bold" if issue.level == Level.ERROR else "yellow bold"
    icon = "✗" if issue.level == Level.ERROR else "⚠"

    # Header
    header = Text()
    header.append(f"{icon} ", style=style)
    header.append(f"{issue.level.value}", style=style)
    header.append(f" [{issue.source}] ", style="dim")
    header.append(issue.message)
    console.print(header)

    # Show file/location if available
    if issue.file:
        console.print(f"   📍 {issue.file}", style="cyan")

    # For markdown_exec issues, always show code (truncated if not verbose)
    if issue.code:
        console.print()
        code_to_show = issue.code

        # In non-verbose mode, show last 10 lines of code
        if not verbose:
            code_lines = issue.code.split("\n")
            if len(code_lines) > 10:
                code_to_show = f"  # ... ({len(code_lines) - 10} lines above)\n" + "\n".join(
                    code_lines[-10:]
                )

        code_to_show = dedent_code(code_to_show)

        syntax = Syntax(
            code_to_show,
            "python",
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
        )
        console.print(Panel(syntax, title="Code Block", border_style="cyan", expand=False))

    # Show output/traceback
    if issue.output:
        output_lines = [line for line in issue.output.split("\n") if line.strip()]

        if verbose:
            # Full traceback in verbose mode
            if len(output_lines) > 15:
                output_text = "\n".join(output_lines[-15:])
                output_text = f"... ({len(output_lines) - 15} lines omitted)\n" + output_text
            else:
                output_text = issue.output
            output_text = dedent_code(output_text)
            console.print(Panel(output_text, title="Traceback", border_style="red", expand=False))
        else:
            # In non-verbose mode, show just the final error line(s)
            # Look for the actual exception (last non-empty line that looks like an error)
            error_lines: list[str] = []
            for line in reversed(output_lines):
                stripped = line.strip()
                if stripped:
                    # Skip log lines (INFO/DEBUG/WARNING/ERROR with dash separator)
                    if re.match(r"^(INFO|DEBUG|WARNING|ERROR)\s+-", stripped):
                        continue

                    error_lines.insert(0, stripped)
                    # Stop after finding the exception line (e.g., "ValueError: ...")
                    # Exception lines typically start with CapitalizedWord followed by colon
                    if (
                        re.match(r"^[A-Z][a-zA-Z]*Error:", stripped)
                        or re.match(r"^[A-Z][a-zA-Z]*Exception:", stripped)
                        or re.match(r"^[A-Z][a-zA-Z]*Warning:", stripped)
                    ):
                        break
                    # Also stop after collecting a few context lines
                    if len(error_lines) >= 3:
                        break

            if error_lines:
                error_summary = "\n".join(error_lines)
                console.print(
                    Panel(
                        error_summary,
                        title="Error Output",
                        border_style="red",
                        expand=False,
                        subtitle="use -v for full traceback",
                        subtitle_align="right",
                    )
                )

    console.print()


# Category display names and icons
INFO_CATEGORY_DISPLAY = {
    InfoCategory.BROKEN_LINK: ("🔗 Broken links", "Link target not found"),
    InfoCategory.ABSOLUTE_LINK: ("🔗 Absolute links", "Left as-is, may not work"),
    InfoCategory.UNRECOGNIZED_LINK: ("🔗 Unrecognized links", "Could not resolve"),
    InfoCategory.MISSING_NAV: ("📄 Pages not in nav", "Not included in navigation"),
    InfoCategory.NO_GIT_LOGS: ("📅 No git history", "git-revision plugin warning"),
}


def print_info_groups(
    console: Console,
    groups: dict[InfoCategory, list[InfoMessage]],
    verbose: bool = False,
    max_files_shown: int = 3,
    max_targets_shown: int = 5,
) -> None:
    """Print grouped INFO messages with compact display."""
    from rich.tree import Tree

    if not groups:
        return

    for category, messages in groups.items():
        title, description = INFO_CATEGORY_DISPLAY.get(category, (category.value, ""))
        count = len(messages)

        # Create a tree for this category
        header = f"[cyan]{title}[/cyan] [dim]({count} files)[/dim]"
        if description:
            header += f" [dim]- {description}[/dim]"

        tree = Tree(header)

        # Group by unique targets if applicable (for link issues)
        if category in (
            InfoCategory.BROKEN_LINK,
            InfoCategory.ABSOLUTE_LINK,
            InfoCategory.UNRECOGNIZED_LINK,
        ):
            # Group by target
            by_target: dict[str, list[InfoMessage]] = {}
            for msg in messages:
                target = msg.target or "unknown"
                if target not in by_target:
                    by_target[target] = []
                by_target[target].append(msg)

            # Sort by count (most files first), then by name
            sorted_targets = sorted(by_target.items(), key=lambda x: (-len(x[1]), x[0]))

            # Limit targets shown unless verbose
            targets_to_show = sorted_targets if verbose else sorted_targets[:max_targets_shown]
            remaining_targets = len(sorted_targets) - len(targets_to_show)

            for target, target_msgs in targets_to_show:
                target_count = len(target_msgs)
                suggestion = target_msgs[0].suggestion

                # Compact format: just show target and count
                if verbose:
                    target_label = f"[yellow]'{target}'[/yellow]"
                    if suggestion:
                        target_label += f" [dim]→ '{suggestion}'[/dim]"
                    target_label += f" [dim]({target_count})[/dim]"
                    branch = tree.add(target_label)
                    for msg in target_msgs:
                        branch.add(f"[dim]{msg.file}[/dim]")
                else:
                    # Very compact: single line per target
                    target_label = f"[yellow]'{target}'[/yellow] [dim]({target_count} files)[/dim]"
                    if suggestion:
                        target_label += f" [dim]→ '{suggestion}'[/dim]"
                    tree.add(target_label)

            if remaining_targets > 0:
                tree.add(f"[dim]... and {remaining_targets} more targets[/dim]")
        else:
            # Simple list of files
            files_to_show = messages if verbose else messages[:max_files_shown]
            for msg in files_to_show:
                tree.add(f"[dim]{msg.file}[/dim]")
            if not verbose and len(messages) > max_files_shown:
                tree.add(f"[dim]... and {len(messages) - max_files_shown} more[/dim]")

        console.print(tree)
        console.print()


def truncate_line(line: str, max_len: int = 60) -> str:
    """Truncate line for display, keeping useful part."""
    line = line.strip()
    line = re.sub(r"^\[stderr\]\s*", "", line)
    line = re.sub(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+\s*-\s*", "", line)
    line = re.sub(r"^[\w.]+\s*-\s*(INFO|WARNING|ERROR)\s*-\s*", "", line)
    if len(line) > max_len:
        return line[:max_len] + "..."
    return line


def print_summary(
    console: Console,
    issues: list[Issue],
    build_info: BuildInfo,
    verbose: bool = False,
) -> None:
    """Print the summary footer with build info and hints."""
    error_count = sum(1 for i in issues if i.level == Level.ERROR)
    warning_count = sum(1 for i in issues if i.level == Level.WARNING)

    if issues:
        console.print("─" * 40, style="dim")
        summary = Text("Summary: ")
        if error_count:
            summary.append(f"{error_count} error(s)", style="red bold")
            if warning_count:
                summary.append(", ")
        if warning_count:
            summary.append(f"{warning_count} warning(s)", style="yellow bold")
        console.print(summary)

    # Always show build info at the end
    console.print()
    if build_info.server_url:
        console.print(f"[bold green]🌐 Server:[/bold green] {build_info.server_url}")
    if build_info.build_dir:
        console.print(f"[bold blue]📁 Output:[/bold blue] {build_info.build_dir}")
    if build_info.build_time:
        console.print(f"[dim]Built in {build_info.build_time}s[/dim]")

    # Show hint for seeing more details
    if issues:
        console.print()
        hints = []
        if not verbose:
            hints.append("[dim]-v[/dim] for verbose output")
        hints.append("[dim]--raw[/dim] for full mkdocs output")
        console.print(f"[dim]Hint: {', '.join(hints)}[/dim]")

        # Check if any markdown_exec issues are missing file context
        missing_file_context = any(
            i.source == "markdown_exec" and i.file and "session" in i.file and ".md" not in i.file
            for i in issues
        )
        if missing_file_context:
            console.print(
                "[dim]Tip: Use [/dim][dim italic]mkdocs build --verbose[/dim italic]"
                "[dim] to see which file contains code block errors[/dim]"
            )


def run_batch_mode(console: Console, args: argparse.Namespace, show_spinner: bool = True) -> int:
    """Run in batch mode - read all input then display results."""
    lines: list[str] = []

    if not show_spinner:
        lines = [line.rstrip() for line in sys.stdin]
    else:
        from rich.live import Live
        from rich.spinner import Spinner

        with Live(console=console, refresh_per_second=10, transient=True) as live:
            for line in sys.stdin:
                lines.append(line.rstrip())
                display_line = truncate_line(line)
                spinner = Spinner("dots", text=f" Building... {display_line}", style="cyan")
                live.update(spinner)

    # Extract build info
    build_info = extract_build_info(lines)

    # Parse issues
    issues = parse_mkdocs_output(lines)

    # Filter if errors-only
    if args.errors_only:
        issues = [i for i in issues if i.level == Level.ERROR]

    # Deduplicate
    seen: set[tuple[Level, str]] = set()
    unique_issues: list[Issue] = []
    for issue in issues:
        key = (issue.level, issue.message[:100])
        if key not in seen:
            seen.add(key)
            unique_issues.append(issue)

    # Parse INFO messages
    info_messages = parse_info_messages(lines)
    info_groups = group_info_messages(info_messages)

    # Print issues
    if not unique_issues:
        console.print("[green]✓ No warnings or errors[/green]")
    else:
        console.print()
        for issue in unique_issues:
            print_issue(console, issue, verbose=args.verbose)

    # Print grouped INFO messages (if any)
    if info_groups:
        print_info_groups(console, info_groups, verbose=args.verbose)

    # Print summary
    print_summary(console, unique_issues, build_info, verbose=args.verbose)

    error_count = sum(1 for i in unique_issues if i.level == Level.ERROR)
    return 1 if error_count else 0


def run_streaming_mode(console: Console, args: argparse.Namespace) -> int:
    """Run in streaming mode - process and display incrementally."""
    from rich.live import Live
    from rich.spinner import Spinner

    # Check if state sharing is enabled
    write_state = getattr(args, "share_state", False)

    processor = StreamingProcessor(
        console=console,
        verbose=args.verbose,
        errors_only=args.errors_only,
        write_state=write_state,
    )

    # Track if we've printed any issues to know if we need a newline
    issues_printed = 0
    build_output_shown = False  # Track if we've shown output for current build

    # Use a spinner while processing, but let processor handle display
    spinner_active = not args.no_progress and not args.no_color
    current_activity = ""

    # Queue for issues to print after build completes
    pending_issues: list[Issue] = []

    def on_issue_queued(issue: Issue) -> None:
        pending_issues.append(issue)

    processor.on_issue = on_issue_queued

    def print_pending_issues() -> None:
        nonlocal issues_printed
        for issue in pending_issues:
            if issues_printed == 0:
                console.print()
            print_issue(console, issue, verbose=args.verbose)
            issues_printed += 1
        pending_issues.clear()

    def print_build_time_inline() -> None:
        """Print build time when build completes."""
        if processor.build_info.build_time:
            console.print(f"[dim]Built in {processor.build_info.build_time}s[/dim]")
        console.print()

    def print_server_url_inline() -> None:
        """Print server URL at the very end for easy visibility."""
        if processor.build_info.server_url:
            console.print(f"[bold green]🌐 Server:[/bold green] {processor.build_info.server_url}")
            console.print()

    def print_info_groups_inline() -> None:
        """Print grouped INFO messages when build completes."""
        if processor.all_info_messages:
            info_groups = group_info_messages(processor.all_info_messages)
            if info_groups:
                print_info_groups(console, info_groups, verbose=args.verbose)

    if spinner_active:
        with Live(console=console, refresh_per_second=10, transient=True) as live:
            for line in sys.stdin:
                # Detect chunk boundaries to know when to show output
                boundary = detect_chunk_boundary(line, None)

                # Process the line FIRST to extract info like server URL
                processor.process_line(line)

                # Update spinner with current activity (include server URL if available)
                display_line = truncate_line(line)
                if display_line != current_activity or processor.build_info.server_url:
                    current_activity = display_line
                    spinner_text = f" {current_activity}"
                    if processor.build_info.server_url:
                        spinner_text += (
                            f"  [bold green]🌐 {processor.build_info.server_url}[/bold green]"
                        )
                    spinner = Spinner("dots", text=spinner_text, style="cyan")
                    live.update(spinner)

                # When build completes or server starts, show results (but only once per build)
                # Order: build time, info groups, then warnings/errors (most visible at end)
                # Server URL is shown in spinner, not printed separately
                if boundary in (ChunkBoundary.BUILD_COMPLETE, ChunkBoundary.SERVER_STARTED):
                    if not build_output_shown:
                        build_output_shown = True
                        live.stop()
                        print_build_time_inline()
                        print_info_groups_inline()
                        print_pending_issues()
                        # Show MCP tip if --share-state is enabled
                        if write_state:
                            state_path = get_state_file_path()
                            if state_path:
                                console.print(
                                    f"[dim]💡 MCP: State shared to {state_path.resolve()}[/dim]"
                                )
                            else:
                                # Fallback to cwd if no project root found
                                console.print(
                                    f"[dim]💡 MCP: State shared to {Path.cwd() / '.mkdocs-output-filter' / 'state.json'}[/dim]"
                                )
                            console.print()
                        live.start()

                # On rebuild start, reset for fresh display
                elif boundary == ChunkBoundary.REBUILD_STARTED:
                    issues_printed = 0
                    build_output_shown = False
    else:
        for line in sys.stdin:
            boundary = detect_chunk_boundary(line, None)
            processor.process_line(line)

            # When build completes or server starts, show results (but only once per build)
            # Order: build time, info groups, then warnings/errors (most visible at end)
            if boundary in (ChunkBoundary.BUILD_COMPLETE, ChunkBoundary.SERVER_STARTED):
                if not build_output_shown:
                    build_output_shown = True
                    print_build_time_inline()
                    print_info_groups_inline()
                    print_pending_issues()
                    # Show MCP tip if --share-state is enabled
                    if write_state:
                        state_path = get_state_file_path()
                        if state_path:
                            console.print(
                                f"[dim]💡 MCP: State shared to {state_path.resolve()}[/dim]"
                            )
                        else:
                            # Fallback to cwd if no project root found
                            console.print(
                                f"[dim]💡 MCP: State shared to {Path.cwd() / '.mkdocs-output-filter' / 'state.json'}[/dim]"
                            )
                        console.print()

            # On rebuild start, reset for fresh display
            elif boundary == ChunkBoundary.REBUILD_STARTED:
                issues_printed = 0
                build_output_shown = False

    # Finalize and get results
    all_issues, build_info = processor.finalize()

    # Print any remaining pending issues (for cases without chunk boundaries)
    # Order: build time, info groups, then warnings/errors (most visible at end)
    if pending_issues:
        print_build_time_inline()
        print_info_groups_inline()
        print_pending_issues()
        print_server_url_inline()

    # If we never saw valid mkdocs output, something went wrong - show raw output
    if not processor.saw_mkdocs_output and processor.raw_buffer:
        console.print("[red bold]Error: mkdocs did not produce expected output[/red bold]")
        console.print()
        console.print("[dim]Raw output:[/dim]")
        for line in processor.raw_buffer:
            console.print(f"  {line}")
        return 1

    # If server crashed (OSError, etc.), show the error
    if processor.saw_server_error and processor.error_lines:
        console.print()
        console.print("[red bold]Server error:[/red bold]")
        for line in processor.error_lines[-20:]:  # Show last 20 lines of error
            console.print(f"  {line}")
        return 1

    # If we were in serve mode but stdin closed, warn user
    if processor.in_serve_mode:
        console.print()
        console.print("[yellow]Server stopped unexpectedly.[/yellow]")

    # Print success message if no issues
    if not all_issues:
        console.print("[green]✓ No warnings or errors[/green]")

    # Print summary (but skip build info if we already printed it inline)
    print_summary(console, all_issues, build_info, verbose=args.verbose)

    error_count = sum(1 for i in all_issues if i.level == Level.ERROR)
    return 1 if error_count else 0


def run_interactive_mode(console: Console, args: argparse.Namespace) -> int:
    """Run in interactive mode with keyboard controls to toggle between filtered/raw."""
    # Check if we're on a Unix-like system with proper terminal support
    if not sys.stdin.isatty() or not hasattr(termios, "tcgetattr"):
        console.print(
            "[yellow]Warning: Interactive mode requires a terminal. Falling back to streaming mode.[/yellow]"
        )
        return run_streaming_mode(console, args)

    # We need to read from stdin (piped mkdocs output) while also reading keyboard input
    # On Unix, we can use /dev/tty for keyboard input while stdin is piped
    try:
        tty_fd = os.open("/dev/tty", os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        console.print(
            "[yellow]Warning: Cannot open /dev/tty for keyboard input. Falling back to streaming mode.[/yellow]"
        )
        return run_streaming_mode(console, args)

    # Shared state
    mode = DisplayMode.FILTERED
    line_queue: Queue[str | None] = Queue()  # None signals EOF
    raw_buffer: list[str] = []  # Store all raw lines for mode switching
    should_quit = threading.Event()
    input_finished = threading.Event()

    # Processor for filtered mode
    processor = StreamingProcessor(
        console=console,
        verbose=args.verbose,
        errors_only=args.errors_only,
    )

    def stdin_reader() -> None:
        """Thread that reads from stdin and queues lines."""
        try:
            while True:
                if should_quit.is_set():
                    break
                line = sys.stdin.readline()
                if not line:  # EOF
                    break
                line_queue.put(line)
        finally:
            line_queue.put(None)  # Signal EOF
            input_finished.set()

    def get_key_nonblocking(fd: int, timeout: float = 0.1) -> str | None:
        """Non-blocking read of a single key from tty."""
        try:
            rlist, _, _ = select.select([fd], [], [], timeout)
            if rlist:
                return os.read(fd, 1).decode("utf-8", errors="ignore")
        except OSError:
            pass
        return None

    # Start stdin reader thread
    reader_thread = threading.Thread(target=stdin_reader, daemon=True)
    reader_thread.start()

    # Set up terminal for raw input
    old_settings = termios.tcgetattr(tty_fd)
    try:
        tty.setraw(tty_fd)

        # Print initial status
        console.print(
            f"[dim]─── Interactive mode: [bold]{'FILTERED' if mode == DisplayMode.FILTERED else 'RAW'}[/bold] "
            "│ Press 'r' for raw, 'f' for filtered, 'q' to quit ───[/dim]"
        )
        console.print()

        issues_printed = 0

        while not should_quit.is_set():
            # Check for keyboard input
            key = get_key_nonblocking(tty_fd)
            if key:
                if key.lower() == "q":
                    should_quit.set()
                    break
                elif key.lower() == "r" and mode != DisplayMode.RAW:
                    mode = DisplayMode.RAW
                    console.print()
                    console.print("[dim]─── Switched to RAW mode ───[/dim]")
                    # Print buffered raw lines
                    for raw_line in raw_buffer:
                        console.print(raw_line.rstrip())
                elif key.lower() == "f" and mode != DisplayMode.FILTERED:
                    mode = DisplayMode.FILTERED
                    console.print()
                    console.print("[dim]─── Switched to FILTERED mode ───[/dim]")
                    # Re-display issues
                    for issue in processor.all_issues:
                        print_issue(console, issue, verbose=args.verbose)

            # Process queued lines
            try:
                line = line_queue.get(timeout=0.05)
                if line is None:  # EOF
                    break

                # Store in raw buffer
                raw_buffer.append(line)
                if len(raw_buffer) > 10000:  # Limit buffer size
                    raw_buffer = raw_buffer[-10000:]

                # Process based on mode
                if mode == DisplayMode.RAW:
                    console.print(line.rstrip())
                else:
                    # Process through streaming processor
                    old_issue_count = len(processor.all_issues)
                    processor.process_line(line)
                    new_issue_count = len(processor.all_issues)

                    # Print any new issues
                    for issue in processor.all_issues[old_issue_count:new_issue_count]:
                        if issues_printed == 0:
                            console.print()
                        print_issue(console, issue, verbose=args.verbose)
                        issues_printed += 1

            except Empty:
                continue

    finally:
        # Restore terminal settings
        termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_settings)
        os.close(tty_fd)

    # Wait for reader thread to finish
    reader_thread.join(timeout=1.0)

    # Finalize
    all_issues, build_info = processor.finalize()

    # Print success message if no issues
    if not all_issues:
        console.print("[green]✓ No warnings or errors[/green]")

    # Print summary
    print_summary(console, all_issues, build_info, verbose=args.verbose)

    error_count = sum(1 for i in all_issues if i.level == Level.ERROR)
    return 1 if error_count else 0


def main() -> int:
    """Main entry point for the CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Filter mkdocs output to show only warnings and errors",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    mkdocs build --verbose 2>&1 | mkdocs-output-filter
    mkdocs build --verbose 2>&1 | mkdocs-output-filter -v
    mkdocs serve 2>&1 | mkdocs-output-filter --share-state
    mkdocs build 2>&1 | mkdocs-output-filter --errors-only

    # MCP server mode (for code agents)
    mkdocs-output-filter --mcp --watch
    mkdocs-output-filter --mcp --project-dir /path/to/project

Note: Use --verbose with mkdocs to get file paths for code block errors.
        """,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show full code blocks and tracebacks for markdown_exec issues",
    )
    parser.add_argument(
        "-e", "--errors-only", action="store_true", help="Show only errors, not warnings"
    )
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    parser.add_argument("--no-progress", action="store_true", help="Disable progress spinner")
    parser.add_argument(
        "--raw", action="store_true", help="Pass through raw mkdocs output without filtering"
    )
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="Enable streaming mode for mkdocs serve (processes output incrementally)",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Force batch mode (wait for all input before processing)",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Interactive mode: toggle between filtered/raw with keyboard (r=raw, f=filtered, q=quit)",
    )
    parser.add_argument(
        "--share-state",
        action="store_true",
        help="Write state to .mkdocs-output-filter/state.json for MCP server access",
    )
    parser.add_argument(
        "--state-dir",
        type=str,
        help="Directory for state file (default: auto-detect from mkdocs.yml location, or cwd)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Run as MCP server for code agent integration (use with --project-dir or --watch)",
    )
    parser.add_argument(
        "--project-dir",
        type=str,
        help="Project directory for MCP server mode (requires --mcp)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="MCP watch mode: read state from .mkdocs-output-filter/state.json (requires --mcp)",
    )
    parser.add_argument(
        "--pipe",
        action="store_true",
        help="MCP pipe mode: read mkdocs output from stdin (requires --mcp)",
    )
    args = parser.parse_args()

    # MCP server mode - delegate to mcp_server module
    if args.mcp:
        from mkdocs_filter.mcp_server import run_mcp_server

        return run_mcp_server(
            project_dir=args.project_dir,
            pipe_mode=args.pipe,
            watch_mode=args.watch,
            state_dir=args.state_dir,
        )

    try:
        # Raw mode - just pass through everything
        if args.raw:
            for line in sys.stdin:
                print(line, end="")
            return 0

        # When piped, Rich may not detect terminal width properly
        console = Console(
            force_terminal=not args.no_color,
            no_color=args.no_color,
            width=120 if sys.stdin.isatty() is False else None,
            soft_wrap=True,
        )

        # Interactive mode
        if args.interactive:
            return run_interactive_mode(console, args)

        # Determine mode: streaming vs batch
        # Default to streaming unless --batch is specified
        use_streaming = args.streaming or not args.batch

        if use_streaming:
            return run_streaming_mode(console, args)
        else:
            show_spinner = not args.no_progress and not args.no_color
            return run_batch_mode(console, args, show_spinner=show_spinner)

    except KeyboardInterrupt:
        # Clean exit on Ctrl+C
        print("\n\nInterrupted.", file=sys.stderr)
        return 130  # Standard exit code for SIGINT


if __name__ == "__main__":
    sys.exit(main())
