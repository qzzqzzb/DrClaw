"""Tests for the DebugLogger."""

from __future__ import annotations

from pathlib import Path

from drclaw.agent.debug import DebugLogger, make_debug_logger
from drclaw.providers.base import LLMResponse, ToolCallRequest
from tests.conftest import read_jsonl_entries

# ---------------------------------------------------------------------------
# Basic event logging
# ---------------------------------------------------------------------------


def test_log_session_start(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path)
    dl.log_session_start("cli:main", "anthropic/claude-sonnet-4-5", agent_id="main")
    dl.close()

    entries = read_jsonl_entries(path)
    start = entries[0]
    assert start["type"] == "session_start"
    assert start["session_key"] == "cli:main"
    assert start["agent_id"] == "main"
    assert start["model"] == "anthropic/claude-sonnet-4-5"
    assert "ts" in start


def test_log_request_summary(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path, full=False)
    messages = [
        {"role": "system", "content": "You are the DrClaw Main Agent. " + "x" * 600},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "hello"},
    ]
    tools = [
        {"type": "function", "function": {"name": "a"}},
        {"type": "function", "function": {"name": "b"}},
    ]
    dl.log_request(0, messages, tools, agent_id="student:demo:researcher", session_key="web:c1")
    dl.close()

    entries = read_jsonl_entries(path)
    req = entries[0]
    assert req["type"] == "llm_request"
    assert req["iteration"] == 0
    assert req["agent_id"] == "student:demo:researcher"
    assert req["session_key"] == "web:c1"
    assert req["message_count"] == 4
    assert req["roles"] == ["system", "user", "assistant", "user"]
    assert len(req["system_prompt_preview"]) == 500
    assert req["last_user_message"] == "hello"
    assert req["tool_count"] == 2
    # Summary mode must NOT include full messages/tools
    assert "messages" not in req
    assert "tools" not in req


def test_log_request_full(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path, full=True)
    messages = [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "hello"},
    ]
    tools = [{"type": "function", "function": {"name": "a"}}]
    dl.log_request(0, messages, tools, agent_id="main", session_key="cli:main")
    dl.close()

    entries = read_jsonl_entries(path)
    req = entries[0]
    assert req["type"] == "llm_request"
    assert req["agent_id"] == "main"
    assert req["session_key"] == "cli:main"
    assert req["messages"] == messages
    assert req["tools"] == tools


def test_log_response(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path)
    response = LLMResponse(
        content="Hello!",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=100,
        output_tokens=50,
    )
    dl.log_response(0, response, agent_id="student:demo:researcher", session_key="web:c1")
    dl.close()

    entries = read_jsonl_entries(path)
    resp = entries[0]
    assert resp["type"] == "llm_response"
    assert resp["iteration"] == 0
    assert resp["agent_id"] == "student:demo:researcher"
    assert resp["session_key"] == "web:c1"
    assert resp["content"] == "Hello!"
    assert resp["tool_calls"] == []
    assert resp["stop_reason"] == "end_turn"
    assert resp["input_tokens"] == 100
    assert resp["output_tokens"] == 50

    # session_end should have accumulated totals
    end = entries[1]
    assert end["total_input_tokens"] == 100
    assert end["total_output_tokens"] == 50


def test_log_response_with_tool_calls(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path)
    response = LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id="tc1", name="echo", arguments={"text": "hi"})],
        stop_reason="tool_use",
        input_tokens=20,
        output_tokens=10,
    )
    dl.log_response(0, response)
    dl.close()

    entries = read_jsonl_entries(path)
    resp = entries[0]
    assert resp["tool_calls"] == [{"name": "echo", "arguments": {"text": "hi"}}]


def test_log_tool_exec(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path)
    dl.log_tool_exec(
        1,
        "list_projects",
        {},
        "Found 3 projects",
        agent_id="student:demo:researcher",
        session_key="web:c1",
    )
    dl.close()

    entries = read_jsonl_entries(path)
    te = entries[0]
    assert te["type"] == "tool_exec"
    assert te["iteration"] == 1
    assert te["agent_id"] == "student:demo:researcher"
    assert te["session_key"] == "web:c1"
    assert te["tool_name"] == "list_projects"
    assert te["arguments"] == {}
    assert te["result"] == "Found 3 projects"


def test_log_consolidation(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path)
    dl.log_consolidation_request([{"role": "user", "content": "a"}] * 5)
    response = LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(
                id="m1",
                name="save_memory",
                arguments={"history_entry": "x", "memory_update": "y"},
            )
        ],
        stop_reason="tool_use",
        input_tokens=200,
        output_tokens=50,
    )
    dl.log_consolidation_response(response)
    dl.close()

    entries = read_jsonl_entries(path)
    creq = entries[0]
    assert creq["type"] == "consolidation_request"
    assert creq["message_count"] == 5

    cresp = entries[1]
    assert cresp["type"] == "consolidation_response"
    assert cresp["input_tokens"] == 200
    assert cresp["output_tokens"] == 50
    assert len(cresp["tool_calls"]) == 1


def test_close_writes_session_end(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path)
    # Log two responses to accumulate tokens
    dl.log_response(0, LLMResponse("a", [], "end_turn", 100, 40))
    dl.log_response(1, LLMResponse("b", [], "end_turn", 50, 20))
    dl.close()

    entries = read_jsonl_entries(path)
    end = entries[-1]
    assert end["type"] == "session_end"
    assert end["total_input_tokens"] == 150
    assert end["total_output_tokens"] == 60


def test_close_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path)
    dl.log_session_start("cli:test", "test-model")
    dl.close()
    dl.close()  # Should not raise

    entries = read_jsonl_entries(path)
    # Only one session_end
    ends = [e for e in entries if e["type"] == "session_end"]
    assert len(ends) == 1


def test_listener_receives_written_entries(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path)
    seen: list[dict] = []
    dl.add_listener(seen.append)

    dl.log_response(
        0,
        LLMResponse("hi", [], "end_turn", 1, 1),
        agent_id="student:demo:researcher",
        session_key="web:c1",
    )
    dl.close()

    assert seen
    assert seen[0]["type"] == "llm_response"
    assert seen[0]["agent_id"] == "student:demo:researcher"
    assert seen[0]["session_key"] == "web:c1"
    assert "ts" in seen[0]


def test_full_lifecycle(tmp_path: Path) -> None:
    path = tmp_path / "debug.jsonl"
    dl = DebugLogger(path, full=False)

    dl.log_session_start("cli:main", "anthropic/claude-sonnet-4-5")
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    dl.log_request(0, msgs, None)
    dl.log_response(
        0,
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="tc1", name="echo", arguments={"text": "ping"})],
            stop_reason="tool_use",
            input_tokens=30,
            output_tokens=15,
        ),
    )
    dl.log_tool_exec(0, "echo", {"text": "ping"}, "ping")
    dl.log_request(1, msgs, None)
    dl.log_response(1, LLMResponse("Done!", [], "end_turn", 25, 10))
    dl.close()

    entries = read_jsonl_entries(path)
    types = [e["type"] for e in entries]
    assert types == [
        "session_start",
        "llm_request",
        "llm_response",
        "tool_exec",
        "llm_request",
        "llm_response",
        "session_end",
    ]
    assert entries[-1]["total_input_tokens"] == 55
    assert entries[-1]["total_output_tokens"] == 25

    # All entries are valid JSON (already parsed successfully)
    for entry in entries:
        assert "ts" in entry


# ---------------------------------------------------------------------------
# make_debug_logger factory
# ---------------------------------------------------------------------------


def test_factory_creates_unique_paths(tmp_path: Path) -> None:
    debug_dir = tmp_path / "debug"
    path_a, logger_a = make_debug_logger(debug_dir, "main")
    path_b, logger_b = make_debug_logger(debug_dir, "proj-abc123")
    logger_a.close()
    logger_b.close()

    assert path_a != path_b
    assert "main" in path_a.name
    assert "proj-abc123" in path_b.name
    assert path_a.exists()
    assert path_b.exists()


def test_factory_creates_debug_dir(tmp_path: Path) -> None:
    debug_dir = tmp_path / "nested" / "debug"
    assert not debug_dir.exists()
    _, logger = make_debug_logger(debug_dir, "test")
    logger.close()
    assert debug_dir.exists()


def test_factory_sanitizes_agent_id(tmp_path: Path) -> None:
    debug_dir = tmp_path / "debug"
    path, logger = make_debug_logger(debug_dir, "cli:proj-abc/123")
    logger.close()
    assert ":" not in path.name
    assert "/" not in path.stem


def test_factory_per_agent_isolation(tmp_path: Path) -> None:
    debug_dir = tmp_path / "debug"
    _, logger_a = make_debug_logger(debug_dir, "agent-a")
    _, logger_b = make_debug_logger(debug_dir, "agent-b")
    logger_a.log_session_start("cli:a", "model-a")
    logger_b.log_session_start("cli:b", "model-b")
    logger_a.close()
    logger_b.close()

    files = list(debug_dir.glob("*.jsonl"))
    assert len(files) == 2
    for f in files:
        entries = read_jsonl_entries(f)
        starts = [e for e in entries if e["type"] == "session_start"]
        assert len(starts) == 1


def test_factory_full_mode(tmp_path: Path) -> None:
    debug_dir = tmp_path / "debug"
    _, logger = make_debug_logger(debug_dir, "test", full=True)
    messages = [{"role": "user", "content": "hi"}]
    logger.log_request(0, messages, None)
    logger.close()

    files = list(debug_dir.glob("*.jsonl"))
    entries = read_jsonl_entries(files[0])
    req = [e for e in entries if e["type"] == "llm_request"][0]
    assert req["messages"] == messages
