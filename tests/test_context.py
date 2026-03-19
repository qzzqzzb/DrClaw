"""Tests for ContextBuilder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from drclaw.agent.context import ContextBuilder
from drclaw.agent.memory import MemoryStore
from drclaw.agent.skills import SkillsLoader
from drclaw.providers.base import ASSISTANT_PROVIDER_FIELDS_KEY

IDENTITY = "You are DrClaw, a research project manager."


@pytest.fixture
def builder() -> ContextBuilder:
    return ContextBuilder(identity_text=IDENTITY)


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------


def test_callable_identity_text() -> None:
    call_count = 0

    def identity_fn() -> str:
        nonlocal call_count
        call_count += 1
        return f"Dynamic identity #{call_count}"

    cb = ContextBuilder(identity_text=identity_fn)
    p1 = cb.build_system_prompt()
    p2 = cb.build_system_prompt()
    assert "Dynamic identity #1" in p1
    assert "Dynamic identity #2" in p2
    assert call_count == 2


def test_build_system_prompt_no_memory(builder: ContextBuilder) -> None:
    prompt = builder.build_system_prompt()
    assert IDENTITY in prompt
    assert "# Global Operating Policy" in prompt
    assert "default to uv" in prompt
    assert "seek help from the user or caller" in prompt
    assert "Memory" not in prompt


def test_build_system_prompt_with_memory(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "project")
    store.write_long_term("User prefers concise answers.")
    cb = ContextBuilder(identity_text=IDENTITY, memory_store=store)

    prompt = cb.build_system_prompt()
    assert IDENTITY in prompt
    assert "Memory" in prompt
    assert "User prefers concise answers." in prompt


def test_build_system_prompt_empty_memory_store(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "project")  # no content written
    cb = ContextBuilder(identity_text=IDENTITY, memory_store=store)

    prompt = cb.build_system_prompt()
    assert IDENTITY in prompt
    assert "Memory" not in prompt


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


def test_build_messages_ordering(builder: ContextBuilder) -> None:
    history: list[dict[str, Any]] = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    msgs = builder.build_messages(history, "what's next?")

    assert msgs[0]["role"] == "system"
    assert msgs[1] == history[0]
    assert msgs[2] == history[1]
    assert msgs[3]["role"] == "user"  # runtime context
    assert msgs[4]["role"] == "user"
    assert msgs[4]["content"] == "what's next?"


def test_build_messages_no_history(builder: ContextBuilder) -> None:
    msgs = builder.build_messages([], "hello")
    assert len(msgs) == 3  # system + runtime + user
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"] == "hello"


def test_build_messages_channel_absent(builder: ContextBuilder) -> None:
    msgs = builder.build_messages([], "hi")
    runtime = msgs[1]["content"]
    assert "Channel" not in runtime
    assert "Chat ID" not in runtime


def test_build_messages_channel_present(builder: ContextBuilder) -> None:
    msgs = builder.build_messages([], "hi", channel="cli", chat_id="main")
    runtime = msgs[1]["content"]
    assert "Channel: cli" in runtime
    assert "Chat ID: main" in runtime


# ---------------------------------------------------------------------------
# _build_runtime_context
# ---------------------------------------------------------------------------


def test_runtime_context_tag() -> None:
    ctx = ContextBuilder._build_runtime_context(None, None)
    assert ctx.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)


def test_runtime_context_has_timestamp() -> None:
    ctx = ContextBuilder._build_runtime_context(None, None)
    assert "Current Time:" in ctx
    assert "(UTC)" in ctx


def test_runtime_context_includes_webui_language_hint() -> None:
    ctx = ContextBuilder._build_runtime_context(
        "web", "chat-1", {"webui_language": "zh"}
    )
    assert "WebUI Preferred Response Language: zh" in ctx


def test_runtime_context_ignores_invalid_webui_language_hint() -> None:
    ctx = ContextBuilder._build_runtime_context(
        "web", "chat-1", {"webui_language": "de"}
    )
    assert "WebUI Preferred Response Language" not in ctx


def test_runtime_context_includes_active_agents() -> None:
    ctx = ContextBuilder._build_runtime_context(
        "web",
        "chat-1",
        {
            "active_agents": [
                {"id": "proj:cat-1", "name": "Cat", "role": "project"},
                {"id": "equip:web", "name": "Web Search", "role": "equipment"},
            ]
        },
    )
    assert "Active Agents:" in ctx
    assert "Cat | id=proj:cat-1 | role=project" in ctx
    assert "Web Search | id=equip:web | role=equipment" in ctx


# ---------------------------------------------------------------------------
# add_assistant_message
# ---------------------------------------------------------------------------


def test_add_assistant_message_no_tool_calls(builder: ContextBuilder) -> None:
    msgs: list[dict[str, Any]] = []
    builder.add_assistant_message(msgs, "Hello!")
    assert len(msgs) == 1
    assert msgs[0] == {"role": "assistant", "content": "Hello!"}
    assert "tool_calls" not in msgs[0]


def test_add_assistant_message_with_tool_calls(builder: ContextBuilder) -> None:
    tool_calls = [{"id": "tc1", "type": "function", "function": {"name": "foo", "arguments": "{}"}}]
    msgs: list[dict[str, Any]] = []
    builder.add_assistant_message(msgs, None, tool_calls=tool_calls)
    assert msgs[0]["tool_calls"] == tool_calls


def test_add_assistant_message_with_provider_metadata(builder: ContextBuilder) -> None:
    msgs: list[dict[str, Any]] = []
    builder.add_assistant_message(
        msgs,
        None,
        tool_calls=[],
        assistant_metadata={"reasoning_content": "step by step"},
    )
    assert msgs[0][ASSISTANT_PROVIDER_FIELDS_KEY] == {"reasoning_content": "step by step"}


# ---------------------------------------------------------------------------
# add_tool_result
# ---------------------------------------------------------------------------


def test_add_tool_result(builder: ContextBuilder) -> None:
    msgs: list[dict[str, Any]] = []
    builder.add_tool_result(msgs, "tc1", "read_file", "file contents")
    assert msgs[0] == {
        "role": "tool",
        "tool_call_id": "tc1",
        "name": "read_file",
        "content": "file contents",
    }


# ---------------------------------------------------------------------------
# Skills injection
# ---------------------------------------------------------------------------


def _make_skill(skill_dir: Path, name: str, frontmatter: str, body: str) -> None:
    """Helper: create a skill directory with SKILL.md."""
    d = skill_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}")


def test_no_skills_loader_no_skills_sections(builder: ContextBuilder) -> None:
    prompt = builder.build_system_prompt()
    assert "Active Skills" not in prompt
    assert "# Skills" not in prompt


def test_always_skills_in_prompt(tmp_path: Path) -> None:
    skills_dir = tmp_path / "workspace" / "skills"
    _make_skill(skills_dir, "note-taker", "always: true\ndescription: Takes notes", "Take notes.")
    loader = SkillsLoader(workspace=tmp_path / "workspace")
    cb = ContextBuilder(identity_text=IDENTITY, skills_loader=loader)

    prompt = cb.build_system_prompt()
    assert "# Active Skills" in prompt
    assert "Take notes." in prompt


def test_skills_summary_in_prompt(tmp_path: Path) -> None:
    skills_dir = tmp_path / "workspace" / "skills"
    _make_skill(skills_dir, "search", "description: Web search", "Search the web.")
    loader = SkillsLoader(workspace=tmp_path / "workspace")
    cb = ContextBuilder(identity_text=IDENTITY, skills_loader=loader)

    prompt = cb.build_system_prompt()
    assert "# Skills" in prompt
    assert 'available="false"' not in prompt or 'available="true"' in prompt
    assert "read_file tool" in prompt
    assert "<skills>" in prompt


def test_no_always_skills_no_active_section(tmp_path: Path) -> None:
    skills_dir = tmp_path / "workspace" / "skills"
    _make_skill(skills_dir, "search", "description: Web search", "Search the web.")
    loader = SkillsLoader(workspace=tmp_path / "workspace")
    cb = ContextBuilder(identity_text=IDENTITY, skills_loader=loader)

    prompt = cb.build_system_prompt()
    assert "# Active Skills" not in prompt
    assert "# Skills" in prompt  # summary still present


def test_empty_summary_no_skills_section(tmp_path: Path) -> None:
    loader = SkillsLoader(workspace=tmp_path / "workspace")
    cb = ContextBuilder(identity_text=IDENTITY, skills_loader=loader)

    prompt = cb.build_system_prompt()
    assert "# Active Skills" not in prompt
    assert "# Skills" not in prompt


def test_always_skill_empty_body(tmp_path: Path) -> None:
    skills_dir = tmp_path / "workspace" / "skills"
    _make_skill(skills_dir, "empty", "always: true\ndescription: Empty", "")
    loader = SkillsLoader(workspace=tmp_path / "workspace")
    cb = ContextBuilder(identity_text=IDENTITY, skills_loader=loader)

    prompt = cb.build_system_prompt()
    assert "# Active Skills" not in prompt


def test_skills_preamble_mentions_install(tmp_path: Path) -> None:
    skills_dir = tmp_path / "workspace" / "skills"
    _make_skill(skills_dir, "search", "description: Web search", "Search the web.")
    loader = SkillsLoader(workspace=tmp_path / "workspace")
    cb = ContextBuilder(identity_text=IDENTITY, skills_loader=loader)

    prompt = cb.build_system_prompt()
    assert "check the <install> element" in prompt
    assert "installation commands" in prompt


def test_project_notes_injected(tmp_path: Path) -> None:
    notes_file = tmp_path / "PROJECT.md"
    notes_file.write_text("This project studies transformer architectures.")
    cb = ContextBuilder(identity_text=IDENTITY, project_notes_file=notes_file)

    prompt = cb.build_system_prompt()
    assert "# Project Notes" in prompt
    assert "transformer architectures" in prompt


def test_project_notes_absent_when_missing(tmp_path: Path) -> None:
    notes_file = tmp_path / "PROJECT.md"  # does not exist
    cb = ContextBuilder(identity_text=IDENTITY, project_notes_file=notes_file)

    prompt = cb.build_system_prompt()
    assert "Project Notes" not in prompt


def test_project_notes_absent_when_empty(tmp_path: Path) -> None:
    notes_file = tmp_path / "PROJECT.md"
    notes_file.write_text("   ")
    cb = ContextBuilder(identity_text=IDENTITY, project_notes_file=notes_file)

    prompt = cb.build_system_prompt()
    assert "Project Notes" not in prompt


def test_project_notes_skipped_on_read_error(tmp_path: Path) -> None:
    from unittest.mock import patch

    notes_file = tmp_path / "PROJECT.md"
    notes_file.write_text("valid content")
    cb = ContextBuilder(identity_text=IDENTITY, project_notes_file=notes_file)

    with patch.object(Path, "read_text", side_effect=OSError("disk error")):
        prompt = cb.build_system_prompt()
    assert "Project Notes" not in prompt


def test_section_ordering(tmp_path: Path) -> None:
    # Set up memory
    store = MemoryStore(tmp_path / "project")
    store.write_long_term("User likes cats.")

    # Set up always-on skill + another skill for summary
    skills_dir = tmp_path / "workspace" / "skills"
    _make_skill(skills_dir, "always-skill", "always: true\ndescription: Always", "Always on.")
    _make_skill(skills_dir, "optional-skill", "description: Optional", "Optional.")
    loader = SkillsLoader(workspace=tmp_path / "workspace")

    # Set up PROJECT.md
    notes_file = tmp_path / "PROJECT.md"
    notes_file.write_text("Project notes here.")

    cb = ContextBuilder(
        identity_text=IDENTITY,
        memory_store=store,
        skills_loader=loader,
        project_notes_file=notes_file,
    )
    prompt = cb.build_system_prompt()

    i_identity = prompt.index(IDENTITY)
    i_policy = prompt.index("# Global Operating Policy")
    i_memory = prompt.index("# Memory")
    i_project_notes = prompt.index("# Project Notes")
    i_active = prompt.index("# Active Skills")
    i_skills = prompt.index("# Skills")

    assert i_identity < i_policy < i_memory < i_project_notes < i_active < i_skills
