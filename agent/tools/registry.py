"""
Tool Registry â€” central hub for all agent tools.

Solves three problems at once:
  1. Eliminates hand-writing JSON schemas (auto-generated from type hints + docstrings)
  2. Single dispatch point for all tool calls
  3. Enforces: every callable tool has a schema, every schema has an implementation

Features implemented from Tool Calling Implementation.md:
  - @registry.tool decorator for registration
  - Auto JSON Schema from Python type hints
  - Google-style docstring param extraction
  - Schema export for Anthropic / OpenAI / Ollama
  - Parallel execution with semaphore concurrency limit
  - Schema validation via jsonschema
  - Output truncation (prevents context overflow)
  - Detailed logging per call
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any, Callable, get_args, get_origin

from loguru import logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_OUTPUT_CHARS = 8_000   # truncate tool outputs beyond this
MAX_CONCURRENCY = 5        # parallel tool call limit


# ---------------------------------------------------------------------------
# Type â†’ JSON Schema helpers
# ---------------------------------------------------------------------------


def _type_to_schema(annotation: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema fragment."""
    import types
    from typing import Literal, Union

    origin = get_origin(annotation)
    args = get_args(annotation)

    # Optional[X] / Union[X, None]
    if origin is Union or (hasattr(types, "UnionType") and isinstance(annotation, types.UnionType)):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _type_to_schema(non_none[0])

    # Literal["a", "b"] â†’ enum
    if origin is Literal:
        return {"type": "string", "enum": list(args)}

    # list[X]
    if origin is list:
        item = args[0] if args else str
        return {"type": "array", "items": _type_to_schema(item)}

    # dict
    if origin is dict or annotation is dict:
        return {"type": "object"}

    _MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}
    return {"type": _MAP.get(annotation, "string")}


def _parse_param_docs(docstring: str) -> dict[str, str]:
    """Extract param descriptions from a Google-style docstring."""
    param_docs: dict[str, str] = {}
    if not docstring:
        return param_docs

    lines = textwrap.dedent(docstring).splitlines()
    in_args = False
    current: str | None = None

    for line in lines:
        stripped = line.strip()
        if stripped.lower() in ("args:", "arguments:", "parameters:", "params:"):
            in_args = True
            continue
        if in_args and stripped.endswith(":") and not line.startswith(" "):
            in_args = False
            continue
        if in_args:
            m = re.match(r"^\s{2,4}(\w+)[\s(].*?[:)]\s*(.*)", line)
            if m:
                current = m.group(1)
                param_docs[current] = m.group(2).strip()
            elif current and line.startswith(" " * 6):
                param_docs[current] += " " + stripped

    return param_docs


def _build_schema(fn: Callable) -> dict[str, Any]:
    """Auto-generate a JSON Schema from a function's type hints and docstring."""
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ""
    param_docs = _parse_param_docs(doc)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        ann = param.annotation if param.annotation is not inspect.Parameter.empty else str
        prop = _type_to_schema(ann)
        if pname in param_docs:
            prop["description"] = param_docs[pname]
        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(pname)
        properties[pname] = prop

    return {"type": "object", "properties": properties, "required": required}


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


@dataclass
class ToolDefinition:
    name: str
    description: str
    fn: Callable
    is_async: bool
    schema: dict[str, Any]

    def to_simple_schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}

    def to_openai_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.schema,
        }}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """
    Central registry for all agent tools.

    Registration via decorator::

        registry = ToolRegistry()

        @registry.tool
        async def read_file(path: str) -> str:
            '''Read a file from /workspace.
            Args:
                path: Absolute path under /workspace.
            '''
            ...

    Dispatch::

        result = await registry.dispatch("read_file", {"path": "/workspace/main.py"})

    Schema export::

        schemas = registry.tool_schemas()   # Anthropic format (default)
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    # -----------------------------------------------------------------------
    # Registration
    # -----------------------------------------------------------------------

    def tool(
        self,
        fn: Callable | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Any:
        """Decorator â€” registers a function as a tool."""

        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            doc = inspect.getdoc(func) or ""
            tool_desc = description or (doc.split("\n\n")[0].strip() or tool_name)
            schema = _build_schema(func)

            self._tools[tool_name] = ToolDefinition(
                name=tool_name,
                description=tool_desc,
                fn=func,
                is_async=asyncio.iscoroutinefunction(func),
                schema=schema,
            )
            logger.debug(f"[registry] Registered tool: {tool_name}")
            return func

        return decorator(fn) if fn is not None else decorator

    def register(self, tool_def: ToolDefinition) -> None:
        """Register a pre-built ToolDefinition directly."""
        self._tools[tool_def.name] = tool_def

    # -----------------------------------------------------------------------
    # Schema export
    # -----------------------------------------------------------------------

    def tool_schemas(self, provider: str = "openai") -> list[dict[str, Any]]:
        """Export all tool schemas for a given provider."""
        if provider == "openai":
            return [t.to_openai_schema() for t in self._tools.values()]
        else:
            return [t.to_simple_schema() for t in self._tools.values()]

    def tool_descriptions(self) -> dict[str, str]:
        """Return {name: description} mapping for system prompts."""
        return {name: t.description for name, t in self._tools.items()}

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def get(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found. Available: {self.list_names()}")
        return self._tools[name]

    # -----------------------------------------------------------------------
    # Dispatch (single tool)
    # -----------------------------------------------------------------------

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        """
        Execute a tool by name, validate args, return string output.

        - Validates arguments against JSON schema
        - Executes sync or async functions transparently
        - Truncates oversized outputs
        - Returns error strings (never raises) so the LLM can recover
        """
        # Validation
        errors = self._validate(name, arguments)
        if errors:
            err_str = f"Invalid arguments for '{name}': {'; '.join(errors)}"
            logger.warning(f"[registry] {err_str}")
            return err_str

        tool = self.get(name)
        t0 = time.perf_counter()

        try:
            if tool.is_async:
                raw = await tool.fn(**arguments)
            else:
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: tool.fn(**arguments)
                )

            result = _truncate(str(raw))
            elapsed = (time.perf_counter() - t0) * 1000
            logger.debug(f"[registry] {name} â†’ {elapsed:.0f}ms | {result[:80]}")
            return result

        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.warning(f"[registry] {name} raised after {elapsed:.0f}ms: {exc}")
            return f"Error executing '{name}': {exc}"

    # -----------------------------------------------------------------------
    # Parallel dispatch
    # -----------------------------------------------------------------------

    async def dispatch_parallel(
        self,
        calls: list[dict[str, Any]],
        max_concurrency: int = MAX_CONCURRENCY,
    ) -> list[str]:
        """
        Execute multiple tool calls in parallel with a concurrency limit.

        Each call dict: {"name": str, "arguments": dict, "tool_use_id": str}
        Returns results in the same order as the input list.
        """
        semaphore = asyncio.Semaphore(max_concurrency)

        async def one(call: dict[str, Any]) -> str:
            async with semaphore:
                return await self.dispatch(call["name"], call.get("arguments", {}))

        return list(await asyncio.gather(*[one(c) for c in calls]))

    # -----------------------------------------------------------------------
    # Factory: default toolset
    # -----------------------------------------------------------------------

    @classmethod
    def default(cls) -> "ToolRegistry":
        """
        Build and return a registry pre-loaded with all standard tools.
        Imports lazily so individual tool modules are testable standalone.
        """
        registry = cls()

        from agent.tools.filesystem import register_filesystem_tools
        from agent.tools.terminal import register_terminal_tools
        from agent.tools.web_search import register_web_search_tools
        from agent.tools.code_executor import register_code_executor_tools

        register_filesystem_tools(registry)
        register_terminal_tools(registry)
        register_web_search_tools(registry)
        register_code_executor_tools(registry)

        logger.info(f"[registry] Default registry ready â€” {len(registry._tools)} tools: {registry.list_names()}")
        return registry

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _validate(self, name: str, arguments: dict[str, Any]) -> list[str]:
        """Validate arguments against the tool's JSON schema. Returns error list."""
        try:
            import jsonschema  # type: ignore[import]
        except ImportError:
            return []  # skip validation if jsonschema not installed

        try:
            tool = self.get(name)
            validator = jsonschema.Draft7Validator(tool.schema)
            return [
                f"{'.'.join(str(p) for p in e.path) or 'root'}: {e.message}"
                for e in validator.iter_errors(arguments)
            ]
        except KeyError:
            return [f"Unknown tool: {name}"]


def _truncate(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """Truncate oversized tool output, keeping head + tail."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        f"[Output truncated â€” {len(text):,} total chars]\n\n"
        f"{text[:half]}\n\n...\n\n{text[-half:]}"
    )
