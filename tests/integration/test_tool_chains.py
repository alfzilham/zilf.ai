"""
Integration tests for tool chains â€” sequential multi-tool workflows.

Tests:
  - Write â†’ read â†’ delete chain
  - Write â†’ search by content
  - Write multiple â†’ list dir
  - Write Python â†’ run code
  - Error propagation through chains
  - Registry parallel dispatch
"""

from __future__ import annotations

import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Basic chains
# ---------------------------------------------------------------------------


class TestWriteReadDelete:

    @pytest.mark.asyncio
    async def test_write_read_roundtrip(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        path = str(tmp_workspace / "roundtrip.txt")

        write = await reg.dispatch("write_file", {"path": path, "content": "round trip content"})
        assert "OK" in write

        read = await reg.dispatch("read_file", {"path": path})
        assert "round trip content" in read

    @pytest.mark.asyncio
    async def test_write_read_delete_chain(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        path = str(tmp_workspace / "chain.txt")

        await reg.dispatch("write_file", {"path": path, "content": "to be deleted"})
        read = await reg.dispatch("read_file", {"path": path})
        assert "to be deleted" in read

        delete = await reg.dispatch("delete_file", {"path": path})
        assert "OK" in delete

        after = await reg.dispatch("read_file", {"path": path})
        assert "error" in after.lower() or "not found" in after.lower()

    @pytest.mark.asyncio
    async def test_overwrite_chain(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        path = str(tmp_workspace / "overwrite.txt")

        await reg.dispatch("write_file", {"path": path, "content": "version 1"})
        await reg.dispatch("write_file", {"path": path, "content": "version 2"})

        read = await reg.dispatch("read_file", {"path": path})
        assert "version 2" in read
        assert "version 1" not in read


# ---------------------------------------------------------------------------
# Search chains
# ---------------------------------------------------------------------------


class TestSearchChains:

    @pytest.mark.asyncio
    async def test_write_then_search_by_glob(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()

        for name in ["alpha.py", "beta.py", "gamma.md"]:
            await reg.dispatch("write_file", {
                "path": str(tmp_workspace / name),
                "content": f"# {name}",
            })

        result = await reg.dispatch("search_files", {
            "pattern": "*.py",
            "directory": str(tmp_workspace),
        })
        assert "alpha.py" in result
        assert "beta.py" in result
        assert "gamma.md" not in result

    @pytest.mark.asyncio
    async def test_write_then_search_by_content(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()

        await reg.dispatch("write_file", {
            "path": str(tmp_workspace / "auth.py"),
            "content": "def authenticate(user, password): pass",
        })
        await reg.dispatch("write_file", {
            "path": str(tmp_workspace / "utils.py"),
            "content": "def helper(): pass",
        })

        result = await reg.dispatch("search_files", {
            "pattern": "*.py",
            "directory": str(tmp_workspace),
            "contains": "authenticate",
        })
        assert "auth.py" in result
        assert "utils.py" not in result

    @pytest.mark.asyncio
    async def test_search_returns_no_results_message(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()

        result = await reg.dispatch("search_files", {
            "pattern": "*.xyz_nonexistent",
            "directory": str(tmp_workspace),
        })
        assert "no files" in result.lower()


# ---------------------------------------------------------------------------
# List dir chains
# ---------------------------------------------------------------------------


class TestListDirChains:

    @pytest.mark.asyncio
    async def test_write_multiple_then_list(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()

        files = ["file_a.py", "file_b.py", "file_c.txt"]
        for name in files:
            await reg.dispatch("write_file", {
                "path": str(tmp_workspace / name),
                "content": f"# {name}",
            })

        result = await reg.dispatch("list_dir", {"directory": str(tmp_workspace)})
        for name in files:
            assert name in result

    @pytest.mark.asyncio
    async def test_delete_then_list_no_longer_shows_file(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        path = str(tmp_workspace / "temp.py")

        await reg.dispatch("write_file", {"path": path, "content": "temporary"})
        before = await reg.dispatch("list_dir", {"directory": str(tmp_workspace)})
        assert "temp.py" in before

        await reg.dispatch("delete_file", {"path": path})
        after = await reg.dispatch("list_dir", {"directory": str(tmp_workspace)})
        assert "temp.py" not in after


# ---------------------------------------------------------------------------
# Code execution chains
# ---------------------------------------------------------------------------


class TestCodeExecutionChains:

    @pytest.mark.asyncio
    async def test_write_python_then_run(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()
        path = str(tmp_workspace / "greet.py")

        await reg.dispatch("write_file", {
            "path": path,
            "content": "print('hello from chain test')\n",
        })

        result = await reg.dispatch("run_command", {
            "command": f"python {path}",
        })
        assert "hello from chain test" in result

    @pytest.mark.asyncio
    async def test_run_code_snippet(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()

        result = await reg.dispatch("run_code", {
            "code": "x = 6 * 7\nprint(x)",
            "language": "python",
        })
        assert "42" in result


# ---------------------------------------------------------------------------
# Parallel dispatch
# ---------------------------------------------------------------------------


class TestParallelDispatch:

    @pytest.mark.asyncio
    async def test_parallel_writes(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()

        calls = [
            {"name": "write_file", "arguments": {"path": str(tmp_workspace / f"parallel_{i}.txt"), "content": f"content {i}"}}
            for i in range(4)
        ]
        results = await reg.dispatch_parallel(calls)
        assert len(results) == 4
        for r in results:
            assert "OK" in r

    @pytest.mark.asyncio
    async def test_parallel_reads(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()

        # Write files sequentially first
        for i in range(3):
            await reg.dispatch("write_file", {
                "path": str(tmp_workspace / f"pread_{i}.txt"),
                "content": f"value_{i}",
            })

        # Read them in parallel
        calls = [
            {"name": "read_file", "arguments": {"path": str(tmp_workspace / f"pread_{i}.txt")}}
            for i in range(3)
        ]
        results = await reg.dispatch_parallel(calls)
        assert len(results) == 3
        for i, r in enumerate(results):
            assert f"value_{i}" in r


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


class TestErrorPropagation:

    @pytest.mark.asyncio
    async def test_read_missing_file_error_does_not_crash_chain(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()

        # Read a file that doesn't exist
        err = await reg.dispatch("read_file", {"path": str(tmp_workspace / "missing.txt")})
        assert "error" in err.lower() or "not found" in err.lower()

        # Chain continues â€” write a new file
        ok = await reg.dispatch("write_file", {
            "path": str(tmp_workspace / "recovery.txt"),
            "content": "recovered",
        })
        assert "OK" in ok

    @pytest.mark.asyncio
    async def test_parallel_with_one_failing(self, tmp_workspace: Path) -> None:
        from agent.tools.registry import ToolRegistry
        reg = ToolRegistry.default()

        # Write one good file
        await reg.dispatch("write_file", {
            "path": str(tmp_workspace / "good.txt"),
            "content": "exists",
        })

        calls = [
            {"name": "read_file", "arguments": {"path": str(tmp_workspace / "good.txt")}},
            {"name": "read_file", "arguments": {"path": str(tmp_workspace / "missing.txt")}},
        ]
        results = await reg.dispatch_parallel(calls)
        assert len(results) == 2
        assert "exists" in results[0]
        assert "error" in results[1].lower() or "not found" in results[1].lower()
