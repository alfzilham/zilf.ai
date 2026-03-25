"""
SWE-bench Runner â€” runs the Zilf AI against SWE-bench tasks
and records results using BenchmarkTracker.

SWE-bench evaluates agents on resolving real GitHub issues from popular
Python repositories. Each issue includes a problem description, the
codebase at a specific commit, and a test suite to verify the fix.

Prerequisites:
    pip install swebench
    git clone https://github.com/princeton-nlp/SWE-bench.git

Usage:
    # Quick smoke test (5 tasks)
    python tests/benchmarks/run_swebench.py --limit 5

    # Full evaluation
    python tests/benchmarks/run_swebench.py --split test

    # Compare two versions
    python tests/benchmarks/run_swebench.py compare v1.0 v2.0
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# BenchmarkTracker â€” stores results across runs
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkResult:
    """Result of running the agent on one benchmark task."""

    benchmark_name: str
    agent_version: str
    timestamp: str
    model: str
    total_tasks: int
    resolved: int
    partial: int
    failed: int
    avg_steps: float
    avg_time_seconds: float
    avg_tokens: int
    environment: dict[str, Any] = field(default_factory=dict)
    per_task_details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resolved_percent(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return round(self.resolved / self.total_tasks * 100, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_name": self.benchmark_name,
            "agent_version": self.agent_version,
            "timestamp": self.timestamp,
            "environment": self.environment,
            "results": {
                "total_tasks": self.total_tasks,
                "resolved": self.resolved,
                "resolved_percent": self.resolved_percent,
                "partial": self.partial,
                "failed": self.failed,
                "avg_steps": self.avg_steps,
                "avg_time_seconds": self.avg_time_seconds,
                "avg_tokens": self.avg_tokens,
                "per_task_details": self.per_task_details[:50],  # cap for readability
            },
        }

    def to_markdown(self) -> str:
        return (
            f"## Benchmark: {self.benchmark_name}\n\n"
            f"**Agent:** `{self.agent_version}`  |  **Model:** `{self.model}`\n\n"
            f"| Metric | Value |\n"
            f"|--------|-------|\n"
            f"| Total tasks | {self.total_tasks} |\n"
            f"| Resolved | {self.resolved} ({self.resolved_percent}%) |\n"
            f"| Partial | {self.partial} |\n"
            f"| Failed | {self.failed} |\n"
            f"| Avg steps | {self.avg_steps} |\n"
            f"| Avg time | {self.avg_time_seconds:.1f}s |\n"
            f"| Avg tokens | {self.avg_tokens:,} |\n"
        )


class BenchmarkTracker:
    """
    Stores and queries benchmark results across agent versions.

    Results are saved as individual JSON files + an index.json summary.

    Usage::

        tracker = BenchmarkTracker(results_dir="benchmark_results")
        tracker.add_result(result)

        latest = tracker.get_latest("SWE-bench")
        comparison = tracker.compare_versions("SWE-bench", "v1.0", "v2.0")
    """

    def __init__(self, results_dir: str = "benchmark_results") -> None:
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def add_result(self, result: BenchmarkResult) -> Path:
        """Save a benchmark result and update the index."""
        ts = result.timestamp.replace(":", "-").replace(".", "-")[:19]
        filename = f"{result.benchmark_name}_{result.agent_version}_{ts}.json"
        path = self.results_dir / filename
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        self._update_index(result)
        return path

    def get_latest(self, benchmark_name: str) -> BenchmarkResult | None:
        """Return the most recent result for a benchmark, or None."""
        index = self._load_index()
        for entry in index:
            if entry.get("benchmark_name") == benchmark_name:
                return self._entry_to_result(entry)
        return None

    def get_all(self, benchmark_name: str | None = None) -> list[BenchmarkResult]:
        """Return all results, optionally filtered by benchmark name."""
        index = self._load_index()
        results = [self._entry_to_result(e) for e in index]
        if benchmark_name:
            results = [r for r in results if r.benchmark_name == benchmark_name]
        return results

    def compare_versions(
        self,
        benchmark_name: str,
        version_a: str,
        version_b: str,
    ) -> dict[str, Any]:
        """Compare two agent versions on the same benchmark."""
        index = self._load_index()
        results: dict[str, BenchmarkResult] = {}
        for entry in index:
            if entry.get("benchmark_name") == benchmark_name:
                version = entry.get("agent_version", "")
                if version in (version_a, version_b):
                    results[version] = self._entry_to_result(entry)

        if len(results) < 2:
            return {"error": f"Could not find both versions: {version_a}, {version_b}"}

        a, b = results[version_a], results[version_b]
        delta_resolved = b.resolved_percent - a.resolved_percent
        delta_steps = b.avg_steps - a.avg_steps

        return {
            "benchmark": benchmark_name,
            "version_a": version_a,
            "version_b": version_b,
            "resolved_a": a.resolved_percent,
            "resolved_b": b.resolved_percent,
            "delta_resolved_pct": round(delta_resolved, 2),
            "avg_steps_a": a.avg_steps,
            "avg_steps_b": b.avg_steps,
            "delta_steps": round(delta_steps, 2),
            "improvement": delta_resolved > 0,
        }

    # -----------------------------------------------------------------------
    # Private
    # -----------------------------------------------------------------------

    def _load_index(self) -> list[dict[str, Any]]:
        path = self.results_dir / "index.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _update_index(self, result: BenchmarkResult) -> None:
        index = self._load_index()
        index.insert(0, result.to_dict())
        (self.results_dir / "index.json").write_text(
            json.dumps(index, indent=2), encoding="utf-8"
        )

    def _entry_to_result(self, entry: dict[str, Any]) -> BenchmarkResult:
        r = entry.get("results", {})
        return BenchmarkResult(
            benchmark_name=entry.get("benchmark_name", ""),
            agent_version=entry.get("agent_version", ""),
            timestamp=entry.get("timestamp", ""),
            model=entry.get("environment", {}).get("model", ""),
            total_tasks=r.get("total_tasks", 0),
            resolved=r.get("resolved", 0),
            partial=r.get("partial", 0),
            failed=r.get("failed", 0),
            avg_steps=r.get("avg_steps", 0.0),
            avg_time_seconds=r.get("avg_time_seconds", 0.0),
            avg_tokens=r.get("avg_tokens", 0),
            environment=entry.get("environment", {}),
            per_task_details=r.get("per_task_details", []),
        )


# ---------------------------------------------------------------------------
# SWE-bench Runner stub
# ---------------------------------------------------------------------------


async def run_swebench(
    agent: Any,
    agent_version: str = "dev",
    split: str = "test",
    limit: int | None = None,
    results_dir: str = "benchmark_results",
) -> BenchmarkResult:
    """
    Run the agent against SWE-bench tasks.

    This is a stub implementation. To run the real SWE-bench:
        1. Install: pip install swebench
        2. Clone: git clone https://github.com/princeton-nlp/SWE-bench
        3. Replace the _load_tasks() stub below with the actual dataset loader

    Args:
        agent:          Agent instance to evaluate.
        agent_version:  Version string for tracking (e.g. "v1.2").
        split:          Dataset split: "test" | "lite" | "dev".
        limit:          Max number of tasks to run (None = all).
        results_dir:    Where to save results.

    Returns:
        BenchmarkResult with aggregated metrics.
    """
    tasks = _load_tasks(split, limit)
    if not tasks:
        print(f"[swebench] No tasks loaded for split={split!r}. "
              f"Install swebench and download the dataset first.")
        return _empty_result(agent_version)

    tracker = BenchmarkTracker(results_dir)
    task_details: list[dict[str, Any]] = []
    resolved = partial = failed = 0
    total_steps = total_time = total_tokens = 0

    print(f"[swebench] Running {len(tasks)} tasks (split={split}, version={agent_version})")

    for i, task in enumerate(tasks, 1):
        task_id = task.get("instance_id", f"task_{i}")
        description = task.get("problem_statement", "")

        print(f"  [{i}/{len(tasks)}] {task_id}")
        t0 = time.perf_counter()

        try:
            response = await agent.run(description)
            elapsed = time.perf_counter() - t0

            # Evaluate: try to run the test suite
            test_passed = await _evaluate_task(task, response)

            if test_passed:
                resolved += 1
                status = "resolved"
            elif response.success:
                partial += 1
                status = "partial"
            else:
                failed += 1
                status = "failed"

            total_steps  += response.steps_taken
            total_time   += elapsed
            total_tokens += response.total_input_tokens + response.total_output_tokens

            task_details.append({
                "task_id": task_id,
                "status": status,
                "steps": response.steps_taken,
                "time_seconds": round(elapsed, 2),
                "tokens": response.total_input_tokens + response.total_output_tokens,
                "error": response.error,
            })

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            failed += 1
            task_details.append({
                "task_id": task_id,
                "status": "error",
                "steps": 0,
                "time_seconds": round(elapsed, 2),
                "tokens": 0,
                "error": str(exc),
            })

    n = len(tasks)
    result = BenchmarkResult(
        benchmark_name="SWE-bench",
        agent_version=agent_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        model=getattr(agent.llm, "model", "unknown"),
        total_tasks=n,
        resolved=resolved,
        partial=partial,
        failed=failed,
        avg_steps=round(total_steps / n, 2) if n else 0,
        avg_time_seconds=round(total_time / n, 2) if n else 0,
        avg_tokens=round(total_tokens / n) if n else 0,
        environment={
            "model": getattr(agent.llm, "model", "unknown"),
            "split": split,
            "max_steps": getattr(agent, "max_steps", 30),
        },
        per_task_details=task_details,
    )

    path = tracker.add_result(result)
    print(f"\n[swebench] Results saved to: {path}")
    print(result.to_markdown())
    return result


# ---------------------------------------------------------------------------
# Stubs â€” replace with real SWE-bench dataset loading
# ---------------------------------------------------------------------------


def _load_tasks(split: str, limit: int | None) -> list[dict[str, Any]]:
    """
    Load SWE-bench tasks. Returns empty list if swebench is not installed.
    Replace this with: from swebench.harness.utils import load_swebench_dataset
    """
    try:
        from swebench.harness.utils import load_swebench_dataset  # type: ignore[import]
        dataset = load_swebench_dataset(name="princeton-nlp/SWE-bench", split=split)
        tasks = list(dataset)
        return tasks[:limit] if limit else tasks
    except ImportError:
        return []
    except Exception as exc:
        print(f"[swebench] Could not load dataset: {exc}")
        return []


async def _evaluate_task(task: dict[str, Any], response: Any) -> bool:
    """
    Run the task's test suite against the agent's changes.
    Stub â€” real implementation uses docker to run pytest in the repo.
    """
    # In production: spin up docker container with patched repo and run tests
    return response.success


def _empty_result(agent_version: str) -> BenchmarkResult:
    return BenchmarkResult(
        benchmark_name="SWE-bench",
        agent_version=agent_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        model="unknown",
        total_tasks=0, resolved=0, partial=0, failed=0,
        avg_steps=0.0, avg_time_seconds=0.0, avg_tokens=0,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run SWE-bench evaluation")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run evaluation")
    run_parser.add_argument("--version", default="dev")
    run_parser.add_argument("--split", default="test", choices=["test", "lite", "dev"])
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument("--results-dir", default="benchmark_results")

    cmp_parser = subparsers.add_parser("compare", help="Compare two versions")
    cmp_parser.add_argument("version_a")
    cmp_parser.add_argument("version_b")
    cmp_parser.add_argument("--benchmark", default="SWE-bench")
    cmp_parser.add_argument("--results-dir", default="benchmark_results")

    args = parser.parse_args()

    if args.command == "compare":
        tracker = BenchmarkTracker(args.results_dir)
        result = tracker.compare_versions(args.benchmark, args.version_a, args.version_b)
        print(json.dumps(result, indent=2))
    elif args.command == "run":
        from agent.llm.router import LLMRouter
        from agent.tools.registry import ToolRegistry
        from agent.core.agent import Agent

        llm = LLMRouter.from_env()
        registry = ToolRegistry.default()
        agent = Agent(llm=llm, tool_registry=registry, verbose=False)

        asyncio.run(run_swebench(
            agent=agent,
            agent_version=args.version,
            split=args.split,
            limit=args.limit,
            results_dir=args.results_dir,
        ))
    else:
        parser.print_help()
