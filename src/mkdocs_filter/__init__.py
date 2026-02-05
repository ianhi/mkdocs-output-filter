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
    Issue,
    Level,
    StateFileData,
    StreamingState,
    dedent_code,
    detect_chunk_boundary,
    extract_build_info,
    find_project_root,
    get_state_file_path,
    is_in_multiline_block,
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
    # Parsing functions
    "detect_chunk_boundary",
    "is_in_multiline_block",
    "extract_build_info",
    "parse_mkdocs_output",
    "parse_markdown_exec_issue",
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
        self.seen_issues: set[tuple[Level, str]] = set()
        self.build_info = BuildInfo()
        self.prev_line: str | None = None
        self._pending_display = False

    def process_line(self, line: str) -> None:
        """Process a single line of mkdocs output."""
        line = line.rstrip()
        self.buffer.append(line)
        self.raw_buffer.append(line)

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
        self.seen_issues.clear()
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

        # Filter and dedupe
        for issue in issues:
            if self.errors_only and issue.level != Level.ERROR:
                continue

            key = (issue.level, issue.message[:100])
            if key in self.seen_issues:
                continue

            self.seen_issues.add(key)
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

    # Print issues
    if not unique_issues:
        console.print("[green]✓ No warnings or errors[/green]")
    else:
        console.print()
        for issue in unique_issues:
            print_issue(console, issue, verbose=args.verbose)

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

    def print_build_info_inline() -> None:
        """Print server URL and build info when build completes."""
        if processor.build_info.server_url:
            console.print(f"[bold green]🌐 Server:[/bold green] {processor.build_info.server_url}")
        if processor.build_info.build_time:
            console.print(f"[dim]Built in {processor.build_info.build_time}s[/dim]")
        console.print()

    if spinner_active:
        with Live(console=console, refresh_per_second=10, transient=True) as live:
            for line in sys.stdin:
                # Update spinner with current activity
                display_line = truncate_line(line)
                if display_line != current_activity:
                    current_activity = display_line
                    spinner = Spinner("dots", text=f" {current_activity}", style="cyan")
                    live.update(spinner)

                # Detect chunk boundaries to know when to show output
                boundary = detect_chunk_boundary(line, None)

                # Process the line
                processor.process_line(line)

                # When build completes or server starts, show results
                if boundary in (ChunkBoundary.BUILD_COMPLETE, ChunkBoundary.SERVER_STARTED):
                    live.stop()
                    print_build_info_inline()
                    print_pending_issues()
                    live.start()

                # On rebuild start, reset issue counter for fresh display
                elif boundary == ChunkBoundary.REBUILD_STARTED:
                    issues_printed = 0
    else:
        for line in sys.stdin:
            boundary = detect_chunk_boundary(line, None)
            processor.process_line(line)

            # When build completes or server starts, show results
            if boundary in (ChunkBoundary.BUILD_COMPLETE, ChunkBoundary.SERVER_STARTED):
                print_build_info_inline()
                print_pending_issues()

            # On rebuild start, reset issue counter
            elif boundary == ChunkBoundary.REBUILD_STARTED:
                issues_printed = 0

    # Finalize and get results
    all_issues, build_info = processor.finalize()

    # Print any remaining pending issues (for cases without chunk boundaries)
    if pending_issues:
        print_pending_issues()

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

    # Check if the first argument is 'mcp' to dispatch to MCP server
    if len(sys.argv) > 1 and sys.argv[1] == "mcp":
        # Remove 'mcp' from argv and run MCP server main
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from mkdocs_filter.mcp_server import main as mcp_main

        return mcp_main()

    parser = argparse.ArgumentParser(
        description="Filter mkdocs output to show only warnings and errors",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    mkdocs build --verbose 2>&1 | mkdocs-output-filter
    mkdocs build --verbose 2>&1 | mkdocs-output-filter -v
    mkdocs serve 2>&1 | mkdocs-output-filter
    mkdocs build 2>&1 | mkdocs-output-filter --errors-only

    # MCP server mode (for code agents)
    mkdocs-output-filter mcp --project-dir /path/to/project
    mkdocs build 2>&1 | mkdocs-output-filter mcp --pipe

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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

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
