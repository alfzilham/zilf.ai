"""
Tracer â€” OpenTelemetry-based distributed tracing for the Zilf AI.

Creates spans for:
  - Agent task runs (root span)
  - Each reasoning step (child span)
  - Each tool call (leaf span)
  - LLM API calls (leaf span with token counts)

Falls back to a no-op tracer when OpenTelemetry is not installed,
so the agent works without observability dependencies.

Usage::

    tracer = AgentTracer(service_name="zilf-ai")

    with tracer.task_span(run_id="abc", task="Fix the bug") as span:
        with tracer.step_span(step=1, thought="I should read the file"):
            with tracer.tool_span("read_file", {"path": "/workspace/main.py"}):
                result = await tool.execute()
                tracer.record_tool_result(result)
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Generator


# ---------------------------------------------------------------------------
# No-op fallback (used when opentelemetry is not installed)
# ---------------------------------------------------------------------------


class _NoOpSpan:
    """Span that does nothing â€” keeps agent code working without OTel."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass

    def end(self) -> None:
        pass

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class _NoOpTracer:
    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any) -> Generator[_NoOpSpan, None, None]:
        yield _NoOpSpan()


# ---------------------------------------------------------------------------
# Agent Tracer
# ---------------------------------------------------------------------------


class AgentTracer:
    """
    Thin wrapper around OpenTelemetry that adds agent-specific helpers.

    If OpenTelemetry SDK / exporter packages are not installed,
    all tracing calls are silently ignored.
    """

    def __init__(
        self,
        service_name: str = "zilf-ai",
        service_version: str = "0.1.0",
        exporter: str = "console",           # console | otlp | none
        otlp_endpoint: str = "http://localhost:4317",
    ) -> None:
        self.service_name = service_name
        self._tracer = self._build_tracer(
            service_name, service_version, exporter, otlp_endpoint
        )

    # -----------------------------------------------------------------------
    # Span factories
    # -----------------------------------------------------------------------

    @contextmanager
    def task_span(
        self,
        run_id: str,
        task: str,
        model: str = "",
    ) -> Generator[Any, None, None]:
        """Root span for one complete agent task run."""
        with self._tracer.start_as_current_span("agent.task") as span:
            span.set_attribute("agent.run_id", run_id)
            span.set_attribute("agent.task", task[:200])
            span.set_attribute("agent.model", model)
            span.set_attribute("agent.service", self.service_name)
            try:
                yield span
            except Exception as exc:
                span.record_exception(exc)
                raise

    @contextmanager
    def step_span(
        self,
        step: int,
        thought: str = "",
        action_type: str = "",
    ) -> Generator[Any, None, None]:
        """Child span for one reasoning step (Think â†’ Act â†’ Observe)."""
        with self._tracer.start_as_current_span("agent.step") as span:
            span.set_attribute("agent.step_number", step)
            span.set_attribute("agent.thought", thought[:200])
            span.set_attribute("agent.action_type", action_type)
            try:
                yield span
            except Exception as exc:
                span.record_exception(exc)
                raise

    @contextmanager
    def tool_span(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Generator[Any, None, None]:
        """Leaf span for one tool invocation."""
        t0 = time.perf_counter()
        with self._tracer.start_as_current_span(f"tool.{tool_name}") as span:
            span.set_attribute("tool.name", tool_name)
            # Sanitize arguments â€” never log secrets
            safe_args = {
                k: (str(v)[:100] if not any(s in k.lower() for s in ("key", "token", "secret")) else "[REDACTED]")
                for k, v in arguments.items()
            }
            span.set_attribute("tool.arguments", str(safe_args))
            try:
                yield span
            except Exception as exc:
                span.set_attribute("tool.error", str(exc)[:200])
                span.record_exception(exc)
                raise
            finally:
                elapsed = (time.perf_counter() - t0) * 1000
                span.set_attribute("tool.elapsed_ms", round(elapsed, 2))

    @contextmanager
    def llm_span(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        step: int = 0,
    ) -> Generator[Any, None, None]:
        """Leaf span for one LLM API call."""
        with self._tracer.start_as_current_span("llm.call") as span:
            span.set_attribute("llm.model", model)
            span.set_attribute("llm.input_tokens", input_tokens)
            span.set_attribute("llm.output_tokens", output_tokens)
            span.set_attribute("llm.total_tokens", input_tokens + output_tokens)
            span.set_attribute("agent.step_number", step)
            try:
                yield span
            except Exception as exc:
                span.record_exception(exc)
                raise

    # -----------------------------------------------------------------------
    # Builder
    # -----------------------------------------------------------------------

    def _build_tracer(
        self,
        service_name: str,
        service_version: str,
        exporter: str,
        otlp_endpoint: str,
    ) -> Any:
        if exporter == "none":
            return _NoOpTracer()

        try:
            from opentelemetry import trace  # type: ignore[import]
            from opentelemetry.sdk.resources import Resource  # type: ignore[import]
            from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import]
            from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[import]

            resource = Resource.create({
                "service.name": service_name,
                "service.version": service_version,
            })
            provider = TracerProvider(resource=resource)

            if exporter == "console":
                from opentelemetry.sdk.trace.export import ConsoleSpanExporter  # type: ignore[import]
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            elif exporter == "otlp":
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter  # type: ignore[import]
                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
                )

            trace.set_tracer_provider(provider)
            return trace.get_tracer(service_name, service_version)

        except ImportError:
            from loguru import logger
            logger.debug("[tracer] opentelemetry not installed â€” tracing disabled.")
            return _NoOpTracer()
