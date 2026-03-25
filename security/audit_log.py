"""
Audit Log â€” immutable append-only trail of all agent actions.

Records every:
  - Tool call (name, arguments, result, exit code)
  - LLM completion (model, token counts, truncated prompt/response)
  - Security event (injection attempt, policy violation, anomaly)
  - Agent lifecycle (start, complete, error)

Written as JSON-lines to a file that is never overwritten â€” only appended.
Each entry has a checksum so tampering is detectable.

Usage::

    log = AuditLog(task_id="run_abc123")
    log.record_tool_call("read_file", {"path": "/workspace/main.py"}, "def main()...")
    log.record_llm_call("claude-sonnet-4-5", 500, 200)
    log.record_security_event("prompt_injection", "REDACTED:system_override in README.md")
    log.record_lifecycle("complete", status="success", steps=5)

    events = log.load_events()
    violations = log.security_events()
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    TOOL_CALL     = "tool_call"
    LLM_CALL      = "llm_call"
    SECURITY      = "security_event"
    LIFECYCLE     = "lifecycle"
    ANOMALY       = "anomaly"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditLog:
    """
    Append-only JSONL audit log.

    One file per task run. Each line is a self-contained JSON event
    with a SHA-256 checksum of its content for tamper detection.
    """

    def __init__(
        self,
        task_id: str = "default",
        log_dir: str = ".agent_logs",
    ) -> None:
        self.task_id = task_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.log_dir / f"{task_id}_audit.jsonl"

    # -----------------------------------------------------------------------
    # Writers
    # -----------------------------------------------------------------------

    def record_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: str,
        exit_code: int = 0,
        elapsed_ms: float = 0.0,
        step: int = 0,
    ) -> None:
        """Record a tool invocation and its output."""
        self._write({
            "event_type": EventType.TOOL_CALL,
            "tool_name": tool_name,
            "arguments": self._sanitize_args(arguments),
            "result_preview": result[:500] if result else "",
            "result_length": len(result),
            "exit_code": exit_code,
            "elapsed_ms": round(elapsed_ms, 2),
            "step": step,
        })

    def record_llm_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        step: int = 0,
        action_type: str = "",
    ) -> None:
        """Record an LLM API call (no prompt/response content â€” privacy)."""
        self._write({
            "event_type": EventType.LLM_CALL,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "step": step,
            "action_type": action_type,
        })

    def record_security_event(
        self,
        event_name: str,
        detail: str,
        severity: str = "medium",
        source: str = "",
        blocked: bool = True,
    ) -> None:
        """
        Record a security-relevant event.

        Args:
            event_name: Short label (e.g. "prompt_injection", "path_traversal")
            detail:     Human-readable description of what was detected
            severity:   low | medium | high | critical
            source:     Which tool / layer detected it
            blocked:    Whether the action was blocked (True) or only logged (False)
        """
        self._write({
            "event_type": EventType.SECURITY,
            "event_name": event_name,
            "detail": detail[:300],
            "severity": severity,
            "source": source,
            "blocked": blocked,
        })

    def record_lifecycle(
        self,
        stage: str,
        **kwargs: Any,
    ) -> None:
        """
        Record an agent lifecycle event (start, plan, complete, error).

        Args:
            stage: e.g. "start", "planning", "complete", "error", "max_steps"
            **kwargs: Additional metadata (status, steps, error_message, etc.)
        """
        self._write({
            "event_type": EventType.LIFECYCLE,
            "stage": stage,
            **{k: v for k, v in kwargs.items() if isinstance(v, (str, int, float, bool, type(None)))},
        })

    def record_anomaly(
        self,
        description: str,
        action_history: list[str] | None = None,
    ) -> None:
        """Record an anomaly detected by the AnomalyDetector."""
        self._write({
            "event_type": EventType.ANOMALY,
            "description": description,
            "action_history": (action_history or [])[-10:],  # last 10 actions
        })

    # -----------------------------------------------------------------------
    # Readers
    # -----------------------------------------------------------------------

    def load_events(
        self,
        event_type: EventType | None = None,
    ) -> list[dict[str, Any]]:
        """Load all events, optionally filtered by type."""
        if not self._path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if event_type is None or ev.get("event_type") == event_type.value:
                    events.append(ev)
            except json.JSONDecodeError:
                pass
        return events

    def security_events(self) -> list[dict[str, Any]]:
        """Return all security events from the log."""
        return self.load_events(EventType.SECURITY)

    def tool_calls(self) -> list[dict[str, Any]]:
        """Return all tool call events."""
        return self.load_events(EventType.TOOL_CALL)

    def verify_integrity(self) -> tuple[bool, int, int]:
        """
        Verify checksums of all log entries.

        Returns (all_valid, valid_count, total_count).
        """
        if not self._path.exists():
            return True, 0, 0

        valid = 0
        total = 0
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                ev = json.loads(line)
                stored_checksum = ev.pop("_checksum", None)
                recomputed = self._checksum(ev)
                if stored_checksum == recomputed:
                    valid += 1
            except Exception:
                pass

        return valid == total, valid, total

    def summary(self) -> dict[str, Any]:
        """Return a summary of all logged events."""
        events = self.load_events()
        by_type: dict[str, int] = {}
        security_count = 0
        blocked_count = 0

        for ev in events:
            etype = ev.get("event_type", "unknown")
            by_type[etype] = by_type.get(etype, 0) + 1
            if etype == EventType.SECURITY:
                security_count += 1
                if ev.get("blocked"):
                    blocked_count += 1

        return {
            "task_id": self.task_id,
            "total_events": len(events),
            "by_type": by_type,
            "security_events": security_count,
            "blocked_events": blocked_count,
            "log_path": str(self._path),
        }

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _write(self, payload: dict[str, Any]) -> None:
        """Append one event to the JSONL file with timestamp, ID, and checksum."""
        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "task_id": self.task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        event["_checksum"] = self._checksum(event)
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except OSError as exc:
            from loguru import logger
            logger.warning(f"[audit_log] Could not write event: {exc}")

    @staticmethod
    def _checksum(data: dict[str, Any]) -> str:
        """SHA-256 of the sorted JSON representation (excluding _checksum key)."""
        clean = {k: v for k, v in data.items() if k != "_checksum"}
        serialized = json.dumps(clean, sort_keys=True, default=str).encode()
        return hashlib.sha256(serialized).hexdigest()[:16]

    @staticmethod
    def _sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
        """
        Remove sensitive values from tool arguments before logging.
        Never log API keys, passwords, or large file contents.
        """
        sensitive_keys = {"api_key", "password", "secret", "token", "key", "credential"}
        result: dict[str, Any] = {}
        for k, v in args.items():
            if any(s in k.lower() for s in sensitive_keys):
                result[k] = "[REDACTED]"
            elif isinstance(v, str) and len(v) > 200:
                result[k] = v[:200] + "â€¦ [truncated]"
            else:
                result[k] = v
        return result
