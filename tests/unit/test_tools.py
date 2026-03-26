"""
Unit tests for all agent tools.

Tests:
  - Filesystem: read_file, write_file, list_dir, search_files, delete_file
  - Path safety: directory traversal prevention
  - Terminal: run_command (local fallback)
  - Web search: result formatting, cache, fallback
  - Code executor: run_code
  - Registry: dispatch, schema export, validation
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Filesystem tools
# ---------------------------------------------------------------------------


class TestReadFile:

    @pytest.mark.asyncio
    async def test_reads_existing_file(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import read_file
        f = tmp_workspace / "hello.txt"
        f.write_text("hello world", encoding="utf-8")
        result = await read_file(str(f))
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_returns_error_for_missing_file(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import read_file
        result = await read_file(str(tmp_workspace / "nonexistent.txt"))
        assert "not found" in result.lower() or "error" in result.lower() or "unknown" in result.lower()

    @pytest.mark.asyncio
    async def test_line_range(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import read_file
        f = tmp_workspace / "multi.txt"
        f.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
        result = await read_file(str(f), start_line=2, end_line=3)
        assert "line2" in result
        assert "line4" not in result

    @pytest.mark.asyncio
    async def test_blocks_directory_traversal(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import read_file
        # FIX: _safe_path now returns error string instead of raising ValueError
        result = await read_file(str(tmp_workspace / ".." / ".." / "etc" / "passwd"))
        assert "error" in result.lower() or "not permitted" in result.lower()


class TestWriteFile:

    @pytest.mark.asyncio
    async def test_creates_new_file(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import write_file
        path = str(tmp_workspace / "new.txt")
        result = await write_file(path, "content here")
        assert "OK" in result
        assert Path(path).read_text() == "content here"

    @pytest.mark.asyncio
    async def test_creates_parent_dirs(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import write_file
        path = str(tmp_workspace / "sub" / "dir" / "file.txt")
        result = await write_file(path, "deep file")
        assert "OK" in result
        assert Path(path).exists()

    @pytest.mark.asyncio
    async def test_overwrites_existing_file(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import write_file
        path = str(tmp_workspace / "overwrite.txt")
        await write_file(path, "original")
        await write_file(path, "updated")
        assert Path(path).read_text() == "updated"

    @pytest.mark.asyncio
    async def test_blocks_traversal(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import write_file
        # FIX: _safe_path now returns error string instead of raising ValueError
        result = await write_file(str(tmp_workspace / ".." / ".." / "evil.txt"), "evil")
        assert "error" in result.lower() or "not permitted" in result.lower()


class TestListDir:

    @pytest.mark.asyncio
    async def test_lists_files(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import list_dir
        (tmp_workspace / "a.py").write_text("")
        (tmp_workspace / "b.py").write_text("")
        result = await list_dir(str(tmp_workspace))
        assert "a.py" in result
        assert "b.py" in result

    @pytest.mark.asyncio
    async def test_missing_directory(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import list_dir
        result = await list_dir(str(tmp_workspace / "doesnotexist"))
        assert "error" in result.lower() or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_directory(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import list_dir
        empty = tmp_workspace / "empty_dir"
        empty.mkdir()
        result = await list_dir(str(empty))
        assert "empty" in result.lower()


class TestSearchFiles:

    @pytest.mark.asyncio
    async def test_finds_by_glob(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import search_files
        (tmp_workspace / "main.py").write_text("def main(): pass")
        (tmp_workspace / "utils.py").write_text("def helper(): pass")
        (tmp_workspace / "readme.md").write_text("# Readme")
        result = await search_files("*.py", directory=str(tmp_workspace))
        assert "main.py" in result
        assert "utils.py" in result
        assert "readme.md" not in result

    @pytest.mark.asyncio
    async def test_finds_by_content(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import search_files
        (tmp_workspace / "auth.py").write_text("def authenticate(user): pass")
        (tmp_workspace / "other.py").write_text("def unrelated(): pass")
        result = await search_files("*.py", directory=str(tmp_workspace), contains="authenticate")
        assert "auth.py" in result
        assert "other.py" not in result

    @pytest.mark.asyncio
    async def test_no_matches(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import search_files
        result = await search_files("*.nonexistent", directory=str(tmp_workspace))
        assert "no files" in result.lower()


class TestDeleteFile:

    @pytest.mark.asyncio
    async def test_deletes_existing_file(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import delete_file
        f = tmp_workspace / "to_delete.txt"
        f.write_text("bye")
        result = await delete_file(str(f))
        assert "OK" in result
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_error_on_missing_file(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import delete_file
        result = await delete_file(str(tmp_workspace / "ghost.txt"))
        assert "error" in result.lower() or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_blocks_directory_deletion(self, tmp_workspace: Path) -> None:
        from agent.tools.filesystem import delete_file
        subdir = tmp_workspace / "subdir"
        subdir.mkdir()
        result = await delete_file(str(subdir))
        assert "directory" in result.lower() or "error" in result.lower()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestToolRegistry:

    def test_default_registry_has_all_tools(self) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        expected = {"read_file", "write_file", "list_dir", "search_files",
                    "delete_file", "run_command", "web_search", "run_code"}
        assert expected.issubset(set(reg.list_names()))

    def test_schema_export_simple(self) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        schemas = reg.tool_schemas("simple")
        assert len(schemas) > 0
        for s in schemas:
            assert "name" in s
            assert "description" in s
            assert "input_schema" in s

    def test_schema_export_openai(self) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        schemas = reg.tool_schemas("openai")
        for s in schemas:
            assert s["type"] == "function"
            assert "function" in s

    def test_dispatch_unknown_tool_returns_error(self) -> None:
        import asyncio
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        # FIX: asyncio.run() instead of deprecated get_event_loop().run_until_complete()
        result = asyncio.run(
            reg.dispatch("nonexistent_tool", {})
        )
        assert "not found" in result.lower() or "error" in result.lower() or "unknown" in result.lower()

    @pytest.mark.asyncio
    async def test_dispatch_write_then_read(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        path = str(tmp_workspace / "dispatch_test.txt")
        write_result = await reg.dispatch("write_file", {"path": path, "content": "via registry"})
        assert "OK" in write_result
        read_result = await reg.dispatch("read_file", {"path": path})
        assert "via registry" in read_result

    @pytest.mark.asyncio
    async def test_output_truncation(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry, MAX_OUTPUT_CHARS
        reg = ToolRegistry.default()
        large = "x" * (MAX_OUTPUT_CHARS + 1000)
        path = str(tmp_workspace / "large.txt")
        await reg.dispatch("write_file", {"path": path, "content": large})
        result = await reg.dispatch("read_file", {"path": path})
        assert len(result) <= MAX_OUTPUT_CHARS + 200   # allow truncation header overhead


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------


class TestWebSearch:

    @pytest.mark.asyncio
    async def test_returns_formatted_results(self) -> None:
        from agent.tools.web_search import _format_results
        results = [
            {"title": "Python docs", "url": "https://docs.python.org", "snippet": "Python 3 reference", "score": 1.0},
            {"title": "RealPython", "url": "https://realpython.com", "snippet": "Python tutorials", "score": 0.8},
        ]
        output = _format_results(results, "python tutorial")
        assert "Python docs" in output
        assert "docs.python.org" in output

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        from agent.tools.web_search import _format_results
        output = _format_results([], "empty query")
        assert "no results" in output.lower()

    @pytest.mark.asyncio
    async def test_cache_hit(self) -> None:
        from agent.tools.web_search import _cache, web_search
        _cache.set("cached query:3:basic", "Cached result")
        result = await web_search("cached query", max_results=3, search_depth="basic")
        assert result == "Cached result"


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------


class TestLLMOutputParser:

    def test_parses_clean_json(self) -> None:
        from agent.output.parser import LLMOutputParser, FileReadTool
        parser = LLMOutputParser()
        raw = '{"tool": "read_file", "file_path": "/workspace/main.py"}'
        tool = parser.parse_tool_call(raw)
        assert isinstance(tool, FileReadTool)
        assert tool.file_path == "/workspace/main.py"

    def test_parses_markdown_wrapped_json(self) -> None:
        from agent.output.parser import LLMOutputParser, FileWriteTool
        parser = LLMOutputParser()
        raw = '```json\n{"tool": "write_file", "file_path": "/workspace/out.py", "content": "pass"}\n```'
        tool = parser.parse_tool_call(raw)
        assert isinstance(tool, FileWriteTool)

    def test_parses_json_in_prose(self) -> None:
        from agent.output.parser import LLMOutputParser
        parser = LLMOutputParser()
        raw = 'I will run the tests.\n{"tool": "run_command", "command": "pytest"}\nLet me know.'
        tool = parser.parse_tool_call(raw)
        assert tool is not None
        assert tool.tool == "run_command"  # type: ignore[attr-defined]

    def test_returns_none_for_no_json(self) -> None:
        from agent.output.parser import LLMOutputParser
        parser = LLMOutputParser()
        assert parser.parse_tool_call("No JSON here, just text.") is None

    def test_safe_path_blocks_traversal(self) -> None:
        from agent.output.parser import FileWriteTool
        with pytest.raises(Exception):
            FileWriteTool(tool="write_file", file_path="../../../etc/passwd", content="evil")

    def test_run_command_blocks_dangerous_commands(self) -> None:
        from agent.output.parser import RunCommandTool
        with pytest.raises(Exception):
            RunCommandTool(tool="run_command", command="rm -rf /")

    def test_run_command_executes_safely(self, tmp_workspace: Path) -> None:
        from agent.output.parser import RunCommandTool
        # FIX: _FORBIDDEN is now a proper ClassVar, so instantiation works correctly
        tool = RunCommandTool(
            tool="run_command",
            command="python -c \"print('hello parser')\"",
            working_directory=str(tmp_workspace),
        )
        result = tool.execute()
        assert "hello parser" in result
