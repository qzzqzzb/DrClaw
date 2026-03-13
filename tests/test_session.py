"""Tests for Session dataclass and SessionManager."""

import json
from pathlib import Path

import pytest

from drclaw.session.manager import Session, SessionManager


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def manager(sessions_dir: Path) -> SessionManager:
    return SessionManager(sessions_dir)


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


def test_session_defaults():
    """Fresh Session has empty messages and zero last_consolidated."""
    s = Session(session_key="cli:main")
    assert s.messages == []
    assert s.last_consolidated == 0


def test_get_history_drops_leading_non_user():
    """get_history() strips leading assistant/system messages."""
    s = Session(
        session_key="cli:main",
        messages=[
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "how can I help?"},
        ],
    )
    history = s.get_history()
    assert len(history) == 2
    assert history[0]["role"] == "user"


def test_get_history_empty_session():
    """get_history() on a session with no messages returns empty list."""
    assert Session(session_key="cli:main").get_history() == []


def test_get_history_all_user_start():
    """get_history() is a no-op when messages already start with user."""
    msgs = [{"role": "user", "content": "ping"}, {"role": "assistant", "content": "pong"}]
    s = Session(session_key="cli:main", messages=msgs)
    assert s.get_history() == msgs


def test_get_history_respects_last_consolidated():
    """get_history() skips messages before last_consolidated."""
    s = Session(
        session_key="cli:main",
        messages=[
            {"role": "user", "content": "old1"},
            {"role": "assistant", "content": "old2"},
            {"role": "user", "content": "old3"},
            {"role": "user", "content": "new1"},
            {"role": "assistant", "content": "new2"},
        ],
        last_consolidated=3,
    )
    history = s.get_history()
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "new1"}
    assert history[1] == {"role": "assistant", "content": "new2"}


def test_get_history_max_messages_cap():
    """get_history(max_messages=N) returns at most N messages."""
    msgs = []
    for i in range(10):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    s = Session(session_key="cli:main", messages=msgs)
    history = s.get_history(max_messages=4)
    assert len(history) == 4
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "u8"


def test_get_history_max_messages_aligns_to_user():
    """After max_messages slicing, leading non-user messages are dropped."""
    s = Session(
        session_key="cli:main",
        messages=[
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
        ],
    )
    # max_messages=2 takes last 2: [assistant a2, user u3]
    # re-align drops assistant → [user u3]
    history = s.get_history(max_messages=2)
    assert len(history) == 1
    assert history[0] == {"role": "user", "content": "u3"}


def test_clear_resets_session():
    """clear() empties messages and resets last_consolidated."""
    s = Session(
        session_key="cli:main",
        messages=[
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ],
        last_consolidated=1,
    )
    s.clear()
    assert s.messages == []
    assert s.last_consolidated == 0


# ---------------------------------------------------------------------------
# SessionManager: load / save
# ---------------------------------------------------------------------------


def test_load_nonexistent_returns_fresh(manager: SessionManager):
    """Loading a session that has no file returns a fresh Session."""
    s = manager.load("cli:main")
    assert s.session_key == "cli:main"
    assert s.messages == []
    assert s.last_consolidated == 0


def test_save_and_load_roundtrip(manager: SessionManager):
    """Session survives a save/load cycle with messages and last_consolidated."""
    s = Session(
        session_key="cli:proj-abc",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
        last_consolidated=1,
    )
    manager.save(s)
    loaded = manager.load("cli:proj-abc")
    assert loaded.messages == s.messages
    assert loaded.last_consolidated == 1


def test_jsonl_format_first_line_is_metadata(manager: SessionManager, sessions_dir: Path):
    """JSONL file: line 0 is metadata record, remaining lines are messages."""
    s = Session(
        session_key="cli:main",
        messages=[{"role": "user", "content": "test"}],
        last_consolidated=0,
    )
    manager.save(s)

    lines = (sessions_dir / "cli_main.jsonl").read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    assert meta["_type"] == "metadata"
    assert meta["session_key"] == "cli:main"
    assert meta["last_consolidated"] == 0

    msg = json.loads(lines[1])
    assert msg == {"role": "user", "content": "test"}


def test_session_key_to_filename(manager: SessionManager, sessions_dir: Path):
    """Colon in session key is replaced by underscore in filename."""
    manager.save(Session(session_key="cli:main"))
    assert (sessions_dir / "cli_main.jsonl").exists()


def test_add_message_appends_and_persists(manager: SessionManager):
    """add_message appends to in-memory list and file survives reload."""
    s = manager.load("cli:main")
    manager.add_message(s, {"role": "user", "content": "first"})
    manager.add_message(s, {"role": "assistant", "content": "second"})

    assert len(s.messages) == 2

    reloaded = manager.load("cli:main")
    assert len(reloaded.messages) == 2
    assert reloaded.messages[0]["content"] == "first"
    assert reloaded.messages[1]["content"] == "second"


def test_add_message_appends_not_rewrites(manager: SessionManager, sessions_dir: Path):
    """add_message after first write appends a single line (O(1) path)."""
    s = Session(session_key="cli:main", messages=[{"role": "user", "content": "seed"}])
    manager.save(s)

    line_count_before = len(
        (sessions_dir / "cli_main.jsonl").read_text(encoding="utf-8").splitlines()
    )
    manager.add_message(s, {"role": "assistant", "content": "reply"})
    line_count_after = len(
        (sessions_dir / "cli_main.jsonl").read_text(encoding="utf-8").splitlines()
    )

    assert line_count_after == line_count_before + 1


def test_multiple_sessions_isolated(manager: SessionManager, sessions_dir: Path):
    """Two sessions write to separate files and don't interfere."""
    s1 = Session(session_key="cli:alpha", messages=[{"role": "user", "content": "a"}])
    s2 = Session(session_key="cli:beta", messages=[{"role": "user", "content": "b"}])
    manager.save(s1)
    manager.save(s2)

    assert (sessions_dir / "cli_alpha.jsonl").exists()
    assert (sessions_dir / "cli_beta.jsonl").exists()

    loaded1 = manager.load("cli:alpha")
    loaded2 = manager.load("cli:beta")
    assert loaded1.messages[0]["content"] == "a"
    assert loaded2.messages[0]["content"] == "b"


# ---------------------------------------------------------------------------
# SessionManager: corruption / edge cases
# ---------------------------------------------------------------------------


def test_load_empty_file_returns_fresh(manager: SessionManager, sessions_dir: Path):
    """An empty JSONL file is treated as a missing session."""
    (sessions_dir / "cli_main.jsonl").write_text("", encoding="utf-8")
    s = manager.load("cli:main")
    assert s.messages == []
    assert s.last_consolidated == 0


def test_load_corrupt_metadata_returns_fresh(manager: SessionManager, sessions_dir: Path):
    """Corrupt metadata line produces a fresh session instead of crashing."""
    (sessions_dir / "cli_main.jsonl").write_text("{bad json\n", encoding="utf-8")
    s = manager.load("cli:main")
    assert s.messages == []


def test_load_corrupt_message_line_skipped(manager: SessionManager, sessions_dir: Path):
    """A single corrupt message line is skipped; valid messages are kept."""
    meta = json.dumps({"_type": "metadata", "session_key": "cli:main", "last_consolidated": 0})
    good = json.dumps({"role": "user", "content": "ok"})
    (sessions_dir / "cli_main.jsonl").write_text(f"{meta}\n{good}\n{{corrupt\n", encoding="utf-8")
    s = manager.load("cli:main")
    assert len(s.messages) == 1
    assert s.messages[0]["content"] == "ok"


def test_load_whitespace_only_returns_fresh(manager: SessionManager, sessions_dir: Path):
    """File containing only whitespace is treated as empty."""
    (sessions_dir / "cli_main.jsonl").write_text("   \n\n  \n", encoding="utf-8")
    s = manager.load("cli:main")
    assert s.messages == []


def test_path_traversal_sanitized(manager: SessionManager, sessions_dir: Path):
    """Traversal attempts are neutralised: the resolved path stays inside sessions_dir.

    safe_filename() strips path separators so "../../etc/passwd" becomes
    ".._.._etc_passwd.jsonl" — a plain filename, never an escape.
    The is_relative_to() guard in _path() provides defence-in-depth.
    """
    path = manager._path("../../etc/passwd")
    assert path.resolve().is_relative_to(sessions_dir.resolve())


def test_save_is_atomic(manager: SessionManager, sessions_dir: Path):
    """save() leaves no .tmp file behind on success."""
    s = Session(session_key="cli:main", messages=[{"role": "user", "content": "hi"}])
    manager.save(s)
    assert not (sessions_dir / "cli_main.tmp").exists()
    assert (sessions_dir / "cli_main.jsonl").exists()
