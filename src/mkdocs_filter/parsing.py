"""Parsing functions for mkdocs output.

This module contains all parsing logic used by both the CLI and MCP server.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Level(Enum):
    """Log level for issues."""

    ERROR = "ERROR"
    WARNING = "WARNING"


class InfoCategory(Enum):
    """Categories for important INFO messages that should be shown."""

    BROKEN_LINK = "broken_link"  # Link target not found
    ABSOLUTE_LINK = "absolute_link"  # Absolute link left as-is
    UNRECOGNIZED_LINK = "unrecognized_link"  # Unrecognized relative link
    MISSING_NAV = "missing_nav"  # Page not in nav
    NO_GIT_LOGS = "no_git_logs"  # Git revision plugin can't find logs


@dataclass
class InfoMessage:
    """An important INFO message that should be shown (grouped with similar messages)."""

    category: InfoCategory
    file: str  # The doc file this relates to
    target: str | None = None  # Link target, suggested fix, etc.
    suggestion: str | None = None  # e.g., "Did you mean 'index.md'?"


@dataclass
class Issue:
    """A warning or error from mkdocs output."""

    level: Level
    source: str
    message: str
    file: str | None = None
    code: str | None = None
    output: str | None = None


@dataclass
class BuildInfo:
    """Information extracted from the build output."""

    server_url: str | None = None
    build_dir: str | None = None
    build_time: str | None = None


@dataclass
class StreamingState:
    """State for streaming processor."""

    buffer: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    build_info: BuildInfo = field(default_factory=BuildInfo)
    seen_issues: set[tuple[Level, str]] = field(default_factory=set)
    in_markdown_exec_block: bool = False


class ChunkBoundary(Enum):
    """Types of chunk boundaries in mkdocs output."""

    BUILD_COMPLETE = "build_complete"  # "Documentation built in X seconds"
    SERVER_STARTED = "server_started"  # "Serving on http://..."
    REBUILD_STARTED = "rebuild_started"  # "Detected file changes" or timestamp restart
    ERROR_BLOCK_END = "error_block_end"  # End of multi-line error block
    NONE = "none"


def detect_chunk_boundary(line: str, prev_line: str | None = None) -> ChunkBoundary:
    """Detect if a line marks a chunk boundary."""
    stripped = line.strip()

    # Build completion
    if re.search(r"Documentation built in [\d.]+ seconds", line):
        return ChunkBoundary.BUILD_COMPLETE

    # Server started
    if re.search(r"Serving on https?://", line):
        return ChunkBoundary.SERVER_STARTED

    # Rebuild detection - file changes detected
    if "Detected file changes" in line or "Reloading docs" in line:
        return ChunkBoundary.REBUILD_STARTED

    # Rebuild detection - timestamp with "Building documentation"
    if re.match(r"^\d{4}-\d{2}-\d{2}", stripped) and "Building documentation" in line:
        return ChunkBoundary.REBUILD_STARTED

    # If we see a new INFO/WARNING/ERROR after blank lines following error content
    if prev_line is not None and not prev_line.strip():
        if re.match(r"^(INFO|WARNING|ERROR)\s*-", stripped):
            return ChunkBoundary.ERROR_BLOCK_END
        if re.match(r"^\d{4}-\d{2}-\d{2}.*?(INFO|WARNING|ERROR)", stripped):
            return ChunkBoundary.ERROR_BLOCK_END

    return ChunkBoundary.NONE


def is_in_multiline_block(lines: list[str]) -> bool:
    """Check if we're currently in a multi-line block (like markdown_exec output)."""
    if not lines:
        return False

    # Look for unclosed markdown_exec blocks
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if "markdown_exec" in line and ("WARNING" in line or "ERROR" in line):
            # Found start of markdown_exec block - check if it's closed
            # A block is closed when we see a new INFO/WARNING/ERROR line
            for j in range(i + 1, len(lines)):
                check_line = lines[j].strip()
                if (
                    check_line
                    and not check_line.startswith(" ")
                    and not check_line.startswith("\t")
                ):
                    if re.match(r"^(INFO|WARNING|ERROR)\s*-", check_line):
                        return False  # Block is closed
                    if re.match(r"^\d{4}-\d{2}-\d{2}.*?(INFO|WARNING|ERROR)", check_line):
                        return False  # Block is closed
            return True  # Block still open
    return False


def extract_build_info(lines: list[str]) -> BuildInfo:
    """Extract server URL, build directory, and timing from mkdocs output."""
    info = BuildInfo()
    for line in lines:
        # Server URL: "Serving on http://127.0.0.1:8000/"
        if match := re.search(r"Serving on (https?://\S+)", line):
            info.server_url = match.group(1)
        # Build time: "Documentation built in 78.99 seconds"
        if match := re.search(r"Documentation built in ([\d.]+) seconds", line):
            info.build_time = match.group(1)
        # Build directory from site_dir config or default
        if match := re.search(r"Building documentation to directory: (.+)", line):
            info.build_dir = match.group(1).strip()
    return info


def parse_info_messages(lines: list[str]) -> list[InfoMessage]:
    """Parse important INFO messages that should be shown to the user."""
    messages: list[InfoMessage] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Only process INFO lines (not WARNING/ERROR - those are handled separately)
        if "WARNING" in line or "ERROR" in line:
            i += 1
            continue

        # Pattern: Doc file 'X.md' contains a link 'Y', but the target is not found
        if match := re.search(
            r"Doc file ['\"]([^'\"]+)['\"] contains a link ['\"]([^'\"]+)['\"].*(?:target is not found|not found)",
            line,
        ):
            messages.append(
                InfoMessage(
                    category=InfoCategory.BROKEN_LINK,
                    file=match.group(1),
                    target=match.group(2),
                )
            )
            i += 1
            continue

        # Pattern: Doc file 'X.md' contains an absolute link 'Y', it was left as is
        if match := re.search(
            r"Doc file ['\"]([^'\"]+)['\"] contains an absolute link ['\"]([^'\"]+)['\"].*left as is",
            line,
        ):
            # Check for suggestion on same or next line
            suggestion = None
            if "Did you mean" in line:
                if suggest_match := re.search(r"Did you mean ['\"]([^'\"]+)['\"]", line):
                    suggestion = suggest_match.group(1)
            messages.append(
                InfoMessage(
                    category=InfoCategory.ABSOLUTE_LINK,
                    file=match.group(1),
                    target=match.group(2),
                    suggestion=suggestion,
                )
            )
            i += 1
            continue

        # Pattern: Doc file 'X.md' contains an unrecognized relative link 'Y'
        if match := re.search(
            r"Doc file ['\"]([^'\"]+)['\"] contains an unrecognized relative link ['\"]([^'\"]+)['\"]",
            line,
        ):
            suggestion = None
            if "Did you mean" in line:
                if suggest_match := re.search(r"Did you mean ['\"]([^'\"]+)['\"]", line):
                    suggestion = suggest_match.group(1)
            messages.append(
                InfoMessage(
                    category=InfoCategory.UNRECOGNIZED_LINK,
                    file=match.group(1),
                    target=match.group(2),
                    suggestion=suggestion,
                )
            )
            i += 1
            continue

        # Pattern: [git-revision-date-localized-plugin] 'X.md' has no git logs
        if match := re.search(
            r"\[git-revision-date-localized-plugin\].*['\"]([^'\"]+)['\"].*has no git logs",
            line,
        ):
            messages.append(
                InfoMessage(
                    category=InfoCategory.NO_GIT_LOGS,
                    file=match.group(1),
                )
            )
            i += 1
            continue

        # Pattern: pages not in nav (multi-line block)
        if "pages exist in the docs directory, but are not included" in line:
            # Collect following lines that look like file paths
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                # Lines starting with "- " are file entries
                if next_line.startswith("- "):
                    file_path = next_line[2:].strip()
                    messages.append(
                        InfoMessage(
                            category=InfoCategory.MISSING_NAV,
                            file=file_path,
                        )
                    )
                    i += 1
                elif next_line and not next_line.startswith("-"):
                    # End of the list
                    break
                else:
                    i += 1
            continue

        i += 1

    return messages


def group_info_messages(
    messages: list[InfoMessage],
) -> dict[InfoCategory, list[InfoMessage]]:
    """Group InfoMessages by category."""
    groups: dict[InfoCategory, list[InfoMessage]] = {}
    for msg in messages:
        if msg.category not in groups:
            groups[msg.category] = []
        groups[msg.category].append(msg)
    return groups


def parse_mkdocs_output(lines: list[str]) -> list[Issue]:
    """Parse mkdocs output and extract warnings/errors."""
    issues: list[Issue] = []
    i = 0
    # Track lines that are part of markdown_exec output to skip them
    skip_until = -1

    while i < len(lines):
        if i < skip_until:
            i += 1
            continue

        line = lines[i]

        # Match WARNING or ERROR lines
        if "WARNING" in line or "ERROR" in line:
            # Determine level
            level = Level.ERROR if "ERROR" in line else Level.WARNING

            # Check if this is a markdown_exec error with code block
            if "markdown_exec" in line:
                issue, end_idx = parse_markdown_exec_issue(lines, i, level)
                if issue:
                    issues.append(issue)
                    skip_until = end_idx
                    i = end_idx
                    continue

            # Skip lines that look like they're part of a traceback
            stripped = line.strip()
            if stripped.startswith("raise ") or stripped.startswith("File "):
                i += 1
                continue

            # Regular warning/error
            message = line
            message = re.sub(r"^\[stderr\]\s*", "", message)
            message = re.sub(r"^\d{4}-\d{2}-\d{2}.*?-\s*", "", message)
            message = re.sub(r"^(WARNING|ERROR)\s*-?\s*", "", message)

            if message.strip():
                # Try to extract file path from message
                file_path = None
                if file_match := re.search(r"'([^']+\.md)'", message):
                    file_path = file_match.group(1)
                elif file_match := re.search(r'"([^"]+\.md)"', message):
                    file_path = file_match.group(1)

                issues.append(
                    Issue(level=level, source="mkdocs", message=message.strip(), file=file_path)
                )

        i += 1

    return issues


def parse_markdown_exec_issue(
    lines: list[str], start: int, level: Level
) -> tuple[Issue | None, int]:
    """Parse a markdown_exec warning/error block. Returns (issue, end_index)."""
    # Look backwards to find which file was being processed
    file_path = None
    for j in range(start - 1, max(-1, start - 50), -1):
        prev_line = lines[j]
        # Look for verbose mode "Reading: file.md" message (most reliable)
        if match := re.search(r"DEBUG\s*-\s*Reading:\s*(\S+\.md)", prev_line):
            file_path = match.group(1)
            break
        # Look for breadcrumb that mentions the file
        if match := re.search(r"Generated breadcrumb string:.*\[([^\]]+)\]\(/([^)]+)\)", prev_line):
            potential_file = match.group(2) + ".md"
            file_path = potential_file
            break
        # Or Doc file message
        if match := re.search(r"Doc file '([^']+\.md)'", prev_line):
            file_path = match.group(1)
            break

    # Collect the code block and output sections
    code_lines: list[str] = []
    output_lines: list[str] = []
    in_code = False
    in_output = False
    session_info = None
    line_number = None

    i = start + 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect section markers
        if stripped == "Code block is:":
            in_code = True
            in_output = False
            i += 1
            continue
        if stripped == "Output is:":
            in_code = False
            in_output = True
            i += 1
            continue

        # Stop conditions: any log line (INFO/DEBUG/WARNING/ERROR)
        if re.match(r"^(INFO|DEBUG|WARNING|ERROR)\s*-", stripped):
            break
        if re.match(r"^\d{4}-\d{2}-\d{2}", stripped):
            break
        if re.match(r"^\[stderr\]", stripped):
            break

        # Collect content
        if in_code and stripped:
            code_lines.append(line.rstrip())
        elif in_output and stripped:
            output_lines.append(line.rstrip())
            # Extract session and line info from traceback
            if match := re.search(
                r'File "<code block: session ([^;]+); n(\d+)>", line (\d+)', stripped
            ):
                session_info = match.group(1)
                line_number = match.group(3)

        i += 1

    # Find the actual error message
    error_msg: str = "Code execution failed"
    for line in reversed(output_lines):
        line = line.strip()
        if line and ("Error:" in line or "Exception:" in line) and not line.startswith("File "):
            error_msg = line
            break

    # Build location string
    location_parts: list[str] = []
    if file_path:
        location_parts.append(file_path)
    if session_info:
        location_parts.append(f"session '{session_info}'")
    if line_number:
        location_parts.append(f"line {line_number}")

    return (
        Issue(
            level=level,
            source="markdown_exec",
            message=error_msg,
            file=" → ".join(location_parts) if location_parts else None,
            code="\n".join(code_lines) if code_lines else None,
            output="\n".join(output_lines) if output_lines else None,
        ),
        i,
    )


def dedent_code(code: str) -> str:
    """Remove consistent leading whitespace from code."""
    lines = code.split("\n")
    if not lines:
        return code

    min_indent = float("inf")
    for line in lines:
        if line.strip():
            indent = len(line) - len(line.lstrip())
            min_indent = min(min_indent, indent)

    if min_indent < float("inf"):
        return "\n".join(
            line[int(min_indent) :] if len(line) > min_indent else line for line in lines
        )
    return code


# State file location: .mkdocs-output-filter/state.json in git root (or cwd)
STATE_DIR_NAME = ".mkdocs-output-filter"
STATE_FILE_NAME = "state.json"


def find_git_root() -> Path | None:
    """Find the git repository root by looking for .git directory."""
    cwd = Path.cwd()

    # Check current directory and parents
    for path in [cwd, *cwd.parents]:
        if (path / ".git").exists():
            return path
        # Stop at home directory or root
        if path == Path.home() or path == path.parent:
            break

    return None


def find_project_root() -> Path | None:
    """Find the mkdocs project root by looking for mkdocs.yml."""
    cwd = Path.cwd()

    # Check current directory and parents
    for path in [cwd, *cwd.parents]:
        if (path / "mkdocs.yml").exists():
            return path
        # Stop at home directory or root
        if path == Path.home() or path == path.parent:
            break

    return None


def get_state_file_path(project_dir: Path | None = None) -> Path | None:
    """Get the path to the state file for a project.

    Priority:
    1. Explicit project_dir if provided
    2. Git root (most reliable for cross-directory access)
    3. Current working directory as fallback
    """
    if project_dir is None:
        # Prefer git root - it's predictable and works across subdirectories
        project_dir = find_git_root()

    if project_dir is None:
        # Fall back to cwd
        project_dir = Path.cwd()

    return project_dir / STATE_DIR_NAME / STATE_FILE_NAME


def find_state_file() -> Path | None:
    """Search for an existing state file in common locations.

    Searches (in priority order):
    1. Git root (preferred - works across all subdirectories)
    2. Current working directory
    3. Project root (where mkdocs.yml is found)

    Returns the path to the first state file found, or None.
    """
    state_file = STATE_DIR_NAME + "/" + STATE_FILE_NAME
    candidates: list[Path] = []

    # 1. Git root (preferred - predictable location)
    git_root = find_git_root()
    if git_root:
        candidates.append(git_root)

    # 2. Current directory
    candidates.append(Path.cwd())

    # 3. Project root (where mkdocs.yml is)
    project_root = find_project_root()
    if project_root:
        candidates.append(project_root)

    # Check each candidate for state file
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)

            state_path = resolved / state_file
            if state_path.exists():
                return state_path
        except OSError:
            continue

    return None


def issue_to_dict(issue: Issue) -> dict[str, Any]:
    """Convert an Issue to a JSON-serializable dict."""
    result: dict[str, Any] = {
        "level": issue.level.value,
        "source": issue.source,
        "message": issue.message,
    }
    if issue.file:
        result["file"] = issue.file
    if issue.code:
        result["code"] = issue.code
    if issue.output:
        result["output"] = issue.output
    return result


def issue_from_dict(data: dict[str, Any]) -> Issue:
    """Create an Issue from a dict."""
    return Issue(
        level=Level(data["level"]),
        source=data["source"],
        message=data["message"],
        file=data.get("file"),
        code=data.get("code"),
        output=data.get("output"),
    )


def build_info_to_dict(info: BuildInfo) -> dict[str, Any]:
    """Convert BuildInfo to a JSON-serializable dict."""
    result: dict[str, Any] = {}
    if info.server_url:
        result["server_url"] = info.server_url
    if info.build_dir:
        result["build_dir"] = info.build_dir
    if info.build_time:
        result["build_time"] = info.build_time
    return result


def build_info_from_dict(data: dict[str, Any]) -> BuildInfo:
    """Create BuildInfo from a dict."""
    return BuildInfo(
        server_url=data.get("server_url"),
        build_dir=data.get("build_dir"),
        build_time=data.get("build_time"),
    )


def info_message_to_dict(msg: InfoMessage) -> dict[str, Any]:
    """Convert an InfoMessage to a JSON-serializable dict."""
    result: dict[str, Any] = {
        "category": msg.category.value,
        "file": msg.file,
    }
    if msg.target:
        result["target"] = msg.target
    if msg.suggestion:
        result["suggestion"] = msg.suggestion
    return result


def info_message_from_dict(data: dict[str, Any]) -> InfoMessage:
    """Create an InfoMessage from a dict."""
    return InfoMessage(
        category=InfoCategory(data["category"]),
        file=data["file"],
        target=data.get("target"),
        suggestion=data.get("suggestion"),
    )


@dataclass
class StateFileData:
    """Data stored in the state file for sharing between CLI and MCP server."""

    issues: list[Issue] = field(default_factory=list)
    info_messages: list[InfoMessage] = field(default_factory=list)
    build_info: BuildInfo = field(default_factory=BuildInfo)
    raw_output: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    project_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "issues": [issue_to_dict(i) for i in self.issues],
            "info_messages": [info_message_to_dict(m) for m in self.info_messages],
            "build_info": build_info_to_dict(self.build_info),
            "raw_output": self.raw_output[-500:],  # Keep last 500 lines
            "timestamp": self.timestamp,
            "project_dir": self.project_dir,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateFileData:
        """Create from a dict."""
        return cls(
            issues=[issue_from_dict(i) for i in data.get("issues", [])],
            info_messages=[info_message_from_dict(m) for m in data.get("info_messages", [])],
            build_info=build_info_from_dict(data.get("build_info", {})),
            raw_output=data.get("raw_output", []),
            timestamp=data.get("timestamp", 0),
            project_dir=data.get("project_dir"),
        )


def write_state_file(
    state: StateFileData,
    project_dir: Path | None = None,
) -> Path | None:
    """Write state to the state file.

    Returns the path to the state file, or None if it couldn't be written.
    """
    state_path = get_state_file_path(project_dir)
    if state_path is None:
        return None

    # Ensure directory exists
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # Update project_dir in state
    if project_dir:
        state.project_dir = str(project_dir)
    elif state.project_dir is None:
        root = find_project_root()
        if root:
            state.project_dir = str(root)

    # Write atomically (write to temp, then rename)
    temp_path = state_path.with_suffix(".tmp")
    try:
        with open(temp_path, "w") as f:
            json.dump(state.to_dict(), f, indent=2)
        os.replace(temp_path, state_path)
        return state_path
    except OSError:
        # Clean up temp file if it exists
        try:
            temp_path.unlink()
        except OSError:
            pass
        return None


def read_state_file(project_dir: Path | None = None) -> StateFileData | None:
    """Read state from the state file.

    If project_dir is specified, reads from that location.
    Otherwise, searches for the state file in common locations.

    Returns the state data, or None if the file doesn't exist or can't be read.
    """
    state_path: Path | None
    if project_dir is not None:
        # Explicit project dir - use it directly
        state_path = project_dir / STATE_DIR_NAME / STATE_FILE_NAME
    else:
        # Search for state file in common locations
        state_path = find_state_file()

    if state_path is None or not state_path.exists():
        return None

    try:
        with open(state_path) as f:
            data = json.load(f)
        return StateFileData.from_dict(data)
    except (OSError, json.JSONDecodeError):
        return None


def get_state_file_age(project_dir: Path | None = None) -> float | None:
    """Get the age of the state file in seconds.

    Returns None if the file doesn't exist.
    """
    state = read_state_file(project_dir)
    if state is None:
        return None
    return time.time() - state.timestamp
