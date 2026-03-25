"""
Tool Output Truncation â€” shrinks oversized tool results before they
bloat the context window.

Three strategies:
  1. head_tail_truncate     â€” keep top fraction + bottom fraction (for files)
  2. error_focused_truncate â€” keep error/traceback lines (for terminal output)
  3. hard_truncate          â€” binary token cut (last resort)

Dispatcher `truncate_tool_output()` routes each tool to the right strategy.

Per-tool token budgets (defaults):
  read_file    â†’ 2,000 tokens
  run_command  â†’ 1,500 tokens
  web_search   â†’ 1,000 tokens
  default      â†’ 1,200 tokens

Usage::

    safe_output = truncate_tool_output("run_command", raw_output)
    safe_output = truncate_tool_output("read_file",   file_contents)
"""

from __future__ import annotations

import re

from agent.cost.context_budget import count_tokens

# Per-tool token budgets
TOOL_BUDGETS: dict[str, int] = {
    "read_file":   2_000,
    "run_command": 1_500,
    "web_search":  1_000,
    "run_code":    1_500,
    "default":     1_200,
}

# Patterns that indicate error lines in terminal output
_ERROR_RE = re.compile(
    r"(error|traceback|exception|failed|assert|warning|critical)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 1. Head + Tail truncation
# ---------------------------------------------------------------------------


def head_tail_truncate(
    text: str,
    max_tokens: int,
    ratio: float = 0.55,
) -> str:
    """
    Keep the first `ratio` fraction and the last `1 - ratio` fraction.

    Useful for source files where the top (imports, signatures) and the
    bottom (recent changes) are most informative.

    Args:
        text:       Raw text to truncate.
        max_tokens: Maximum tokens to keep.
        ratio:      Fraction of lines to keep from the head (default 0.55).

    Returns:
        Truncated text with a [... TRUNCATED ...] marker in the middle.
    """
    if count_tokens(text) <= max_tokens:
        return text

    lines  = text.splitlines()
    head_n = int(len(lines) * ratio)
    tail_n = len(lines) - head_n

    head = lines[:head_n]
    tail = lines[-tail_n:] if tail_n > 0 else []

    marker = "[... TRUNCATED ...]"
    # Iteratively drop lines until budget is met
    while count_tokens("\n".join(head + [marker] + tail)) > max_tokens:
        if head:
            head.pop()
        elif tail:
            tail.pop(0)
        else:
            break

    return "\n".join(head + [marker] + tail)


# ---------------------------------------------------------------------------
# 2. Error-focused truncation (terminal / test output)
# ---------------------------------------------------------------------------


def error_focused_truncate(output: str, max_tokens: int) -> str:
    """
    Keep error/traceback lines plus surrounding context; drop passing output.

    Always keeps the first and last 5 lines for orientation.
    Inserts omission markers so the agent knows lines were removed.

    Args:
        output:     Raw terminal or test runner output.
        max_tokens: Maximum tokens to keep.

    Returns:
        Filtered output focused on failures.
    """
    if count_tokens(output) <= max_tokens:
        return output

    lines = output.splitlines()
    keep: set[int] = set()

    # Mark lines containing error keywords + 2-line context around each
    for i, line in enumerate(lines):
        if _ERROR_RE.search(line):
            keep.update(range(max(0, i - 2), min(len(lines), i + 3)))

    # Always keep first and last 5 lines
    keep.update(range(min(5, len(lines))))
    keep.update(range(max(0, len(lines) - 5), len(lines)))

    result_lines: list[str] = []
    prev = -1
    for i in sorted(keep):
        if i > prev + 1:
            skipped = i - prev - 1
            result_lines.append(f"[... {skipped} lines omitted ...]")
        result_lines.append(lines[i])
        prev = i

    result = "\n".join(result_lines)

    # Final safety: hard truncate if still over budget
    if count_tokens(result) > max_tokens:
        result = hard_truncate(result, max_tokens)
    return result


# ---------------------------------------------------------------------------
# 3. Hard token truncation (last resort)
# ---------------------------------------------------------------------------


def hard_truncate(text: str, max_tokens: int) -> str:
    """
    Truncate `text` to at most `max_tokens` tokens using tiktoken.

    Falls back to character-based truncation when tiktoken is unavailable.
    Always appends [OUTPUT TRUNCATED] so the agent knows content was dropped.
    """
    if count_tokens(text) <= max_tokens:
        return text

    reserve = 10  # tokens for the truncation notice
    target  = max_tokens - reserve

    try:
        import tiktoken  # type: ignore[import]
        enc     = tiktoken.get_encoding("cl100k_base")
        tokens  = enc.encode(text)
        truncated = enc.decode(tokens[:target])
    except ImportError:
        # Fallback: ~4 chars per token
        truncated = text[: target * 4]

    return truncated + "\n[OUTPUT TRUNCATED]"


# ---------------------------------------------------------------------------
# 4. Dispatcher
# ---------------------------------------------------------------------------


def truncate_tool_output(tool_name: str, output: str) -> str:
    """
    Route `output` to the appropriate truncation strategy for `tool_name`.

    Routing:
      run_command / run_code â†’ error_focused_truncate
      read_file              â†’ head_tail_truncate
      everything else        â†’ hard_truncate
    """
    max_tokens = TOOL_BUDGETS.get(tool_name, TOOL_BUDGETS["default"])

    if tool_name in ("run_command", "run_code"):
        return error_focused_truncate(output, max_tokens)
    elif tool_name == "read_file":
        return head_tail_truncate(output, max_tokens, ratio=0.55)
    else:
        return hard_truncate(output, max_tokens)
