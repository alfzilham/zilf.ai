"""
Agent Metrics â€” comprehensive evaluation metrics for the Zilf AI.

Implements all 6 core metrics from Agent Metrics.md:
  1. Task Completion Rate (TCR)
  2. Pass@k for code generation
  3. Mean Time to Completion (MTC)
  4. Tool Call Efficiency Ratio
  5. Error Recovery Rate
  6. Harmful Output Rate

Also provides:
  - MetricsCollector  â€” instruments the agent with automatic metric collection
  - MetricsReport     â€” Pydantic model + JSON schema for metrics output
  - RegressionChecker â€” compares current metrics against stored baselines
  - timed_run()       â€” convenience wrapper for benchmark runs

Usage::

    collector = MetricsCollector(task_id="run_001")
    collector.start_task()

    # ... run agent ...

    collector.end_task(success=True)
    collector.record_tool_call("read_file", success=True)
    collector.record_error("FileNotFoundError", recovered=True)

    report = collector.report()
    print(report.to_markdown())

    # Regression check
    checker = RegressionChecker("baselines/v1.json")
    passed, failures = checker.check(report)
"""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# 1. Core metric functions
# ---------------------------------------------------------------------------


def task_completion_rate(successful: int, total: int) -> float:
    """TCR = (successful / total) Ã— 100. Returns 0 if total == 0."""
    if total == 0:
        return 0.0
    return round((successful / total) * 100, 2)


def pass_at_k(n: int, c: int, k: int) -> float:
    """
    Unbiased Pass@k estimator (Chen et al., 2021).

    Args:
        n: Total number of samples generated per problem.
        c: Number of samples that pass the tests.
        k: k in Pass@k.

    Returns:
        Pass@k probability estimate in [0, 1].
    """
    if n - c < k:
        return 1.0
    return 1.0 - math.prod((n - c - i) / (n - i) for i in range(k))


def pass_at_k_bulk(results: list[dict[str, int]], k: int = 1) -> float:
    """
    Compute Pass@k across a list of problems.

    Each dict in `results` must have keys: "n" (total samples), "c" (passing samples).
    """
    if not results:
        return 0.0
    scores = [pass_at_k(r["n"], r["c"], k) for r in results]
    return round(sum(scores) / len(scores), 4)


def mean_time_to_completion(times: list[float]) -> float:
    """MTC = mean of completion times for successful tasks (seconds)."""
    if not times:
        return 0.0
    return round(sum(times) / len(times), 3)


def tool_call_efficiency_ratio(successful: int, total: int) -> float:
    """TCER = successful tool calls / total tool calls."""
    if total == 0:
        return 0.0
    return round(successful / total, 4)


def error_recovery_rate(recovered: int, total: int) -> float:
    """ERR = errors recovered / total errors encountered."""
    if total == 0:
        return 0.0
    return round(recovered / total, 4)


def harmful_output_rate(harmful: int, total: int) -> float:
    """HOR = (harmful outputs / total outputs) Ã— 100."""
    if total == 0:
        return 0.0
    return round((harmful / total) * 100, 4)


# ---------------------------------------------------------------------------
# 2. MetricsReport (matches JSON schema from Agent Metrics.md)
# ---------------------------------------------------------------------------


@dataclass
class MetricsReport:
    """
    Structured metrics report â€” matches the JSON schema defined in Agent Metrics.md.
    """

    report_id: str
    evaluation_run_id: str
    timestamp: str

    # Core metrics
    task_completion_rate: float       # 0â€“100 %
    pass_at_k_score: float            # 0â€“1
    mean_time_to_completion: float    # seconds
    tool_call_efficiency_ratio: float # 0â€“1
    error_recovery_rate: float        # 0â€“1
    harmful_output_rate: float        # 0â€“100 %

    # Aggregation info
    num_tasks_evaluated: int
    num_successful_tasks: int
    total_tool_calls: int
    total_errors_encountered: int

    # Extended (not in schema but useful)
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    avg_steps_per_task: float = 0.0
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "evaluation_run_id": self.evaluation_run_id,
            "metrics": {
                "task_completion_rate": self.task_completion_rate,
                "pass_at_k": self.pass_at_k_score,
                "mean_time_to_completion": self.mean_time_to_completion,
                "tool_call_efficiency_ratio": self.tool_call_efficiency_ratio,
                "error_recovery_rate": self.error_recovery_rate,
                "harmful_output_rate": self.harmful_output_rate,
            },
            "aggregation_info": {
                "num_tasks_evaluated": self.num_tasks_evaluated,
                "num_successful_tasks": self.num_successful_tasks,
                "total_tool_calls": self.total_tool_calls,
                "total_errors_encountered": self.total_errors_encountered,
            },
            "extended": {
                "total_tokens": self.total_tokens,
                "estimated_cost_usd": self.estimated_cost_usd,
                "avg_steps_per_task": self.avg_steps_per_task,
                "model": self.model,
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        return (
            f"## Agent Metrics Report\n\n"
            f"**Run:** `{self.evaluation_run_id}`  |  "
            f"**Generated:** {self.timestamp[:19]}Z\n\n"
            f"### Core Metrics\n\n"
            f"| Metric | Value | Target |\n"
            f"|--------|-------|--------|\n"
            f"| Task Completion Rate | {self.task_completion_rate:.1f}% | 80â€“100% |\n"
            f"| Pass@1 | {self.pass_at_k_score:.3f} | 0.6â€“0.8 |\n"
            f"| Mean Time to Completion | {self.mean_time_to_completion:.2f}s | â€” |\n"
            f"| Tool Call Efficiency | {self.tool_call_efficiency_ratio:.3f} | 0.9â€“1.0 |\n"
            f"| Error Recovery Rate | {self.error_recovery_rate:.3f} | 0.7â€“0.9 |\n"
            f"| Harmful Output Rate | {self.harmful_output_rate:.4f}% | 0â€“0.1% |\n\n"
            f"### Aggregation\n\n"
            f"- Tasks evaluated: **{self.num_tasks_evaluated}**\n"
            f"- Successful: **{self.num_successful_tasks}**\n"
            f"- Total tool calls: **{self.total_tool_calls:,}**\n"
            f"- Total errors: **{self.total_errors_encountered}**\n"
            f"- Total tokens: **{self.total_tokens:,}**\n"
            f"- Estimated cost: **${self.estimated_cost_usd:.4f}**\n"
        )

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.to_json(), encoding="utf-8")


# ---------------------------------------------------------------------------
# 3. MetricsCollector â€” instruments agent runs
# ---------------------------------------------------------------------------


@dataclass
class _TaskRecord:
    task_id: str
    success: bool = False
    elapsed_seconds: float = 0.0
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    _start: float = field(default_factory=time.time)


class MetricsCollector:
    """
    Collects metrics across multiple agent task runs.

    Usage:
        collector = MetricsCollector(run_id="eval_v2")
        collector.start_task("task_001")
        # ... run agent ...
        collector.end_task("task_001", success=True, steps=8)
        collector.record_tool_call("read_file", success=True)
        collector.record_error("SyntaxError", recovered=True)
        report = collector.report()
    """

    def __init__(
        self,
        run_id: str | None = None,
        model: str = "",
    ) -> None:
        self.run_id = run_id or str(uuid.uuid4())[:12]
        self.model = model
        self._tasks: dict[str, _TaskRecord] = {}
        self._tool_calls_total: int = 0
        self._tool_calls_success: int = 0
        self._errors_total: int = 0
        self._errors_recovered: int = 0
        self._harmful_outputs: int = 0
        self._total_outputs: int = 0
        self._passatk_results: list[dict[str, int]] = []
        self._total_tokens: int = 0

    # â”€â”€ Task lifecycle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def start_task(self, task_id: str) -> None:
        self._tasks[task_id] = _TaskRecord(task_id=task_id)

    def end_task(
        self,
        task_id: str,
        success: bool,
        steps: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        if task_id not in self._tasks:
            self._tasks[task_id] = _TaskRecord(task_id=task_id)
        rec = self._tasks[task_id]
        rec.success = success
        rec.elapsed_seconds = round(time.time() - rec._start, 3)
        rec.steps = steps
        rec.input_tokens = input_tokens
        rec.output_tokens = output_tokens
        self._total_tokens += input_tokens + output_tokens

    def record_agent_response(self, task_id: str, response: Any) -> None:
        """Convenience: populate from AgentResponse object."""
        self.end_task(
            task_id=task_id,
            success=getattr(response, "success", False),
            steps=getattr(response, "steps_taken", 0),
            input_tokens=getattr(response, "total_input_tokens", 0),
            output_tokens=getattr(response, "total_output_tokens", 0),
        )

    # â”€â”€ Tool / error / output tracking â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def record_tool_call(self, tool_name: str, success: bool) -> None:
        self._tool_calls_total += 1
        if success:
            self._tool_calls_success += 1

    def record_error(self, error_type: str, recovered: bool) -> None:
        self._errors_total += 1
        if recovered:
            self._errors_recovered += 1

    def record_output(self, is_harmful: bool) -> None:
        self._total_outputs += 1
        if is_harmful:
            self._harmful_outputs += 1

    def record_passatk(self, n: int, c: int) -> None:
        """Record Pass@k data: n=total samples, c=passing samples for one problem."""
        self._passatk_results.append({"n": n, "c": c})

    # â”€â”€ Report â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def report(self, k: int = 1) -> MetricsReport:
        tasks = list(self._tasks.values())
        total = len(tasks)
        successful = sum(1 for t in tasks if t.success)
        completion_times = [t.elapsed_seconds for t in tasks if t.success]
        total_steps = sum(t.steps for t in tasks)

        from observability.cost_tracker import estimate_cost
        cost = estimate_cost(
            sum(t.input_tokens for t in tasks),
            sum(t.output_tokens for t in tasks),
            model=self.model or "unknown",
        )

        return MetricsReport(
            report_id=str(uuid.uuid4())[:12],
            evaluation_run_id=self.run_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            task_completion_rate=task_completion_rate(successful, total),
            pass_at_k_score=pass_at_k_bulk(self._passatk_results, k) if self._passatk_results else 0.0,
            mean_time_to_completion=mean_time_to_completion(completion_times),
            tool_call_efficiency_ratio=tool_call_efficiency_ratio(self._tool_calls_success, self._tool_calls_total),
            error_recovery_rate=error_recovery_rate(self._errors_recovered, self._errors_total),
            harmful_output_rate=harmful_output_rate(self._harmful_outputs, self._total_outputs),
            num_tasks_evaluated=total,
            num_successful_tasks=successful,
            total_tool_calls=self._tool_calls_total,
            total_errors_encountered=self._errors_total,
            total_tokens=self._total_tokens,
            estimated_cost_usd=cost,
            avg_steps_per_task=round(total_steps / total, 2) if total else 0.0,
            model=self.model,
        )

    def reset(self) -> None:
        self._tasks.clear()
        self._tool_calls_total = 0
        self._tool_calls_success = 0
        self._errors_total = 0
        self._errors_recovered = 0
        self._harmful_outputs = 0
        self._total_outputs = 0
        self._passatk_results.clear()
        self._total_tokens = 0


# ---------------------------------------------------------------------------
# 4. RegressionChecker
# ---------------------------------------------------------------------------


# Default thresholds â€” how much each metric can degrade before flagging
DEFAULT_THRESHOLDS: dict[str, float] = {
    "task_completion_rate":       2.0,    # % points
    "pass_at_k":                  0.02,
    "mean_time_to_completion":    5.0,    # seconds (increase allowed)
    "tool_call_efficiency_ratio": 0.02,
    "error_recovery_rate":        0.02,
    "harmful_output_rate":        0.5,    # % points (increase allowed)
}

# For these metrics, LOWER is better â€” flag if they *increase* too much
_LOWER_IS_BETTER = {"mean_time_to_completion", "harmful_output_rate"}


class RegressionChecker:
    """
    Compares a current MetricsReport against stored baseline metrics.

    Usage::

        checker = RegressionChecker("baselines/v1.json")
        checker.save_baseline(current_report)   # first time

        # After next run:
        passed, failures = checker.check(new_report)
        if not passed:
            for f in failures:
                print(f)
    """

    def __init__(
        self,
        baseline_path: str,
        thresholds: dict[str, float] | None = None,
    ) -> None:
        self.baseline_path = Path(baseline_path)
        self.thresholds = thresholds or DEFAULT_THRESHOLDS

    def save_baseline(self, report: MetricsReport) -> None:
        """Save current report as the new baseline."""
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        self.baseline_path.write_text(report.to_json(), encoding="utf-8")

    def load_baseline(self) -> dict[str, float] | None:
        """Load baseline metrics dict, or None if not found."""
        if not self.baseline_path.exists():
            return None
        data = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        return data.get("metrics", {})

    def check(self, current: MetricsReport) -> tuple[bool, list[str]]:
        """
        Compare current metrics against baseline.

        Returns (passed, list_of_failure_messages).
        """
        baseline = self.load_baseline()
        if baseline is None:
            return True, ["No baseline found â€” saving current as baseline."]

        current_metrics = current.to_dict()["metrics"]
        failures: list[str] = []

        for metric, baseline_val in baseline.items():
            current_val = current_metrics.get(metric)
            if current_val is None:
                continue
            threshold = self.thresholds.get(metric, 0)

            if metric in _LOWER_IS_BETTER:
                # Flag if current is significantly higher than baseline
                if current_val > baseline_val + threshold:
                    failures.append(
                        f"REGRESSION: {metric} increased from {baseline_val} "
                        f"to {current_val} (threshold +{threshold})"
                    )
            else:
                # Flag if current is significantly lower than baseline
                if current_val < baseline_val - threshold:
                    failures.append(
                        f"REGRESSION: {metric} dropped from {baseline_val} "
                        f"to {current_val} (threshold -{threshold})"
                    )

        return len(failures) == 0, failures


# ---------------------------------------------------------------------------
# 5. Convenience: timed_run
# ---------------------------------------------------------------------------


async def timed_run(
    agent: Any,
    task_name: str,
    task: str,
    collector: MetricsCollector,
) -> Any:
    """
    Run one agent task, measure wall-clock time, and record to collector.

    Returns the AgentResponse.
    """
    collector.start_task(task_name)
    try:
        response = await agent.run(task)
        collector.record_agent_response(task_name, response)
        return response
    except Exception as exc:
        collector.end_task(task_name, success=False)
        raise
