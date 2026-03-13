"""Tests for drclaw CLI commands."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from drclaw import __version__
from drclaw.cli.app import app
from drclaw.config.loader import load_config, save_config
from drclaw.config.schema import DrClawConfig
from drclaw.cron.service import CronService
from drclaw.cron.types import CronSchedule
from drclaw.models.project import JsonProjectStore
from tests.mocks import MockProvider

runner = CliRunner()


# ---------------------------------------------------------------------------
# onboard
# ---------------------------------------------------------------------------


def test_onboard_fresh(tmp_path: Path) -> None:
    data_dir = tmp_path / "drclaw_data"
    with patch("drclaw.cli.app.get_data_dir", return_value=data_dir):
        result = runner.invoke(app, ["onboard"])
    assert result.exit_code == 0
    assert "Initialized" in result.output
    assert (data_dir / "config.json").exists()
    assert (data_dir / "skills").is_dir()
    assert (data_dir / "assets").is_dir()
    assert (data_dir / "local-skill-hub").is_dir()
    assert (data_dir / "agent-hub").is_dir()
    assert (data_dir / "agent-hub" / "cat" / "AGENT.yaml").is_file()
    assert (data_dir / "local-skill-hub" / "search" / "arxiv" / "fetch" / "SKILL.md").is_file()


def test_onboard_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "drclaw_data"
    data_dir.mkdir()
    save_config(DrClawConfig(data_dir=str(data_dir)), data_dir / "config.json")
    with patch("drclaw.cli.app.get_data_dir", return_value=data_dir):
        result = runner.invoke(app, ["onboard"])
    assert result.exit_code == 0
    assert "Already initialized" in result.output
    assert (data_dir / "skills").is_dir()
    assert (data_dir / "assets").is_dir()
    assert (data_dir / "local-skill-hub").is_dir()
    assert (data_dir / "agent-hub").is_dir()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    save_config(config, tmp_path / "config.json")
    with patch("drclaw.cli.app._load_config", return_value=config):
        result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    # Rich wraps long lines, so join output before checking path
    flat = result.output.replace("\n", "")
    assert __version__ in flat
    assert str(tmp_path) in flat
    assert config.provider.model in flat
    assert "Projects: 0" in flat


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


def test_reset_requires_yes(tmp_path: Path) -> None:
    data_dir = tmp_path / "drclaw_data"
    save_config(DrClawConfig(data_dir=str(data_dir)), data_dir / "config.json")
    (data_dir / "projects.json").write_text("[]\n", encoding="utf-8")

    with patch("drclaw.cli.app.get_data_dir", return_value=data_dir):
        result = runner.invoke(app, ["reset"])

    assert result.exit_code == 1
    assert "destructive" in result.output
    assert (data_dir / "projects.json").exists()


def test_reset_keeps_existing_config_by_default(tmp_path: Path) -> None:
    data_dir = tmp_path / "drclaw_data"
    save_config(
        DrClawConfig(
            data_dir=str(data_dir),
            provider={"model": "openai/gpt-4o-mini", "api_key": "sk-test"},
        ),
        data_dir / "config.json",
    )
    (data_dir / "projects.json").write_text("[]\n", encoding="utf-8")
    (data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (data_dir / "sessions" / "main.jsonl").write_text("", encoding="utf-8")

    with patch("drclaw.cli.app.get_data_dir", return_value=data_dir):
        result = runner.invoke(app, ["reset", "--yes"])

    assert result.exit_code == 0
    assert "kept config.json" in result.output
    loaded = load_config(data_dir / "config.json")
    assert loaded.provider.model == "openai/gpt-4o-mini"
    assert loaded.provider.api_key == "sk-test"
    assert not (data_dir / "projects.json").exists()
    assert not (data_dir / "sessions").exists()


def test_reset_can_restore_default_config(tmp_path: Path) -> None:
    data_dir = tmp_path / "drclaw_data"
    save_config(
        DrClawConfig(
            data_dir=str(data_dir),
            provider={"model": "openai/gpt-4o-mini", "api_key": "sk-test"},
        ),
        data_dir / "config.json",
    )
    (data_dir / "projects.json").write_text("[]\n", encoding="utf-8")

    with patch("drclaw.cli.app.get_data_dir", return_value=data_dir):
        result = runner.invoke(app, ["reset", "--yes", "--reset-config"])

    assert result.exit_code == 0
    assert "default config.json created" in result.output
    loaded = load_config(data_dir / "config.json")
    assert loaded.provider.model == "anthropic/claude-sonnet-4-5"
    assert loaded.provider.api_key == ""
    assert not (data_dir / "projects.json").exists()


def test_reset_memory_only_clears_sessions_and_memory_keeps_state(tmp_path: Path) -> None:
    data_dir = tmp_path / "drclaw_data"
    save_config(
        DrClawConfig(
            data_dir=str(data_dir),
            provider={"model": "openai/gpt-4o-mini", "api_key": "sk-test"},
        ),
        data_dir / "config.json",
    )
    (data_dir / "projects.json").write_text("[]\n", encoding="utf-8")
    (data_dir / "skills" / "custom-skill").mkdir(parents=True, exist_ok=True)
    (data_dir / "skills" / "custom-skill" / "SKILL.md").write_text("test", encoding="utf-8")

    # Main-agent memory/session artifacts.
    (data_dir / "MEMORY.md").write_text("main memory", encoding="utf-8")
    (data_dir / "HISTORY.md").write_text("main history", encoding="utf-8")
    (data_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (data_dir / "sessions" / "main.jsonl").write_text("", encoding="utf-8")

    # Project-agent memory/session artifacts + workspace files that must remain.
    project_dir = data_dir / "projects" / "proj-123"
    (project_dir / "workspace").mkdir(parents=True, exist_ok=True)
    (project_dir / "workspace" / "SOUL.md").write_text("project soul", encoding="utf-8")
    (project_dir / "workspace" / "notes.md").write_text("keep me", encoding="utf-8")
    (project_dir / "MEMORY.md").write_text("project memory", encoding="utf-8")
    (project_dir / "HISTORY.md").write_text("project history", encoding="utf-8")
    (project_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (project_dir / "sessions" / "proj.jsonl").write_text("", encoding="utf-8")

    # Equipment-agent session artifacts.
    equip_sess = data_dir / "runtime" / "equipments" / "r1" / "sessions"
    equip_sess.mkdir(parents=True, exist_ok=True)
    (equip_sess / "equip.jsonl").write_text("", encoding="utf-8")

    with patch("drclaw.cli.app.get_data_dir", return_value=data_dir):
        result = runner.invoke(app, ["reset", "--yes", "--memory-only"])

    assert result.exit_code == 0
    assert "Reset agent memory" in result.output

    # Removed memory/session artifacts.
    assert not (data_dir / "MEMORY.md").exists()
    assert not (data_dir / "HISTORY.md").exists()
    assert not (data_dir / "sessions").exists()
    assert not (project_dir / "MEMORY.md").exists()
    assert not (project_dir / "HISTORY.md").exists()
    assert not (project_dir / "sessions").exists()
    assert not equip_sess.exists()

    # Preserved config and non-memory state.
    loaded = load_config(data_dir / "config.json")
    assert loaded.provider.model == "openai/gpt-4o-mini"
    assert loaded.provider.api_key == "sk-test"
    assert (data_dir / "projects.json").exists()
    assert (data_dir / "skills" / "custom-skill" / "SKILL.md").exists()
    assert (project_dir / "workspace" / "SOUL.md").exists()
    assert (project_dir / "workspace" / "notes.md").exists()


def test_reset_memory_only_cannot_use_reset_config(tmp_path: Path) -> None:
    data_dir = tmp_path / "drclaw_data"
    save_config(DrClawConfig(data_dir=str(data_dir)), data_dir / "config.json")

    with patch("drclaw.cli.app.get_data_dir", return_value=data_dir):
        result = runner.invoke(app, ["reset", "--yes", "--memory-only", "--reset-config"])

    assert result.exit_code == 1
    assert "cannot be used with --memory-only" in result.output


# ---------------------------------------------------------------------------
# projects list
# ---------------------------------------------------------------------------


def test_projects_list_empty(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    with patch("drclaw.cli.app._load_config", return_value=config):
        result = runner.invoke(app, ["projects", "list"])
    assert result.exit_code == 0
    assert "No projects" in result.output


def test_projects_list_with_data(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    store = JsonProjectStore(tmp_path)
    store.create_project("Alpha", "first project")
    store.create_project("Beta", "second project")
    with patch("drclaw.cli.app._load_config", return_value=config):
        result = runner.invoke(app, ["projects", "list"])
    assert result.exit_code == 0
    assert "Alpha" in result.output
    assert "Beta" in result.output


# ---------------------------------------------------------------------------
# projects create
# ---------------------------------------------------------------------------


def test_projects_create(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    with patch("drclaw.cli.app._load_config", return_value=config):
        result = runner.invoke(app, ["projects", "create", "Quantum", "-d", "quantum research"])
    assert result.exit_code == 0
    assert "Quantum" in result.output
    store = JsonProjectStore(tmp_path)
    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0].name == "Quantum"


def test_projects_create_duplicate(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    store = JsonProjectStore(tmp_path)
    store.create_project("Quantum")
    with patch("drclaw.cli.app._load_config", return_value=config):
        result = runner.invoke(app, ["projects", "create", "Quantum"])
    assert result.exit_code == 1
    assert "already exists" in result.output


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


def test_chat_no_message_launches_repl(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    mock_prov = MockProvider()
    with (
        patch("drclaw.cli.app._load_config", return_value=config),
        patch("drclaw.cli.app._make_provider", return_value=mock_prov),
        patch("drclaw.cli.app._run_interactive", new_callable=AsyncMock) as mock_interactive,
    ):
        result = runner.invoke(app, ["chat"])
    assert result.exit_code == 0
    mock_interactive.assert_called_once_with(config, mock_prov, debug_logger=None)


def test_chat_p_without_m_errors(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    mock_prov = MockProvider()
    with (
        patch("drclaw.cli.app._load_config", return_value=config),
        patch("drclaw.cli.app._make_provider", return_value=mock_prov),
    ):
        result = runner.invoke(app, ["chat", "-p", "myproject"])
    assert result.exit_code == 1
    assert "@mention" in result.output


def test_chat_single_message(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    mock_prov = MockProvider()
    with (
        patch("drclaw.cli.app._load_config", return_value=config),
        patch("drclaw.cli.app._make_provider", return_value=mock_prov),
        patch("drclaw.cli.app._chat_main") as mock_chat,
    ):
        result = runner.invoke(app, ["chat", "-m", "hi"])
    assert result.exit_code == 0
    mock_chat.assert_called_once_with(config, mock_prov, "hi", debug_logger=None)


def test_chat_project_message(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    store = JsonProjectStore(tmp_path)
    store.create_project("DeepSea", "ocean research")
    mock_prov = MockProvider()
    with (
        patch("drclaw.cli.app._load_config", return_value=config),
        patch("drclaw.cli.app._make_provider", return_value=mock_prov),
        patch("drclaw.cli.app._chat_project") as mock_chat,
    ):
        result = runner.invoke(app, ["chat", "-m", "explore", "-p", "DeepSea"])
    assert result.exit_code == 0
    mock_chat.assert_called_once_with(config, mock_prov, "DeepSea", "explore", debug_logger=None)


def test_chat_project_not_found(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    mock_prov = MockProvider()
    with (
        patch("drclaw.cli.app._load_config", return_value=config),
        patch("drclaw.cli.app._make_provider", return_value=mock_prov),
    ):
        result = runner.invoke(app, ["chat", "-m", "hi", "-p", "NonExistent"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_chat_ambiguous_project(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    store = JsonProjectStore(tmp_path)
    store.create_project("Ocean Deep")
    store.create_project("Ocean Wide")
    mock_prov = MockProvider()
    with (
        patch("drclaw.cli.app._load_config", return_value=config),
        patch("drclaw.cli.app._make_provider", return_value=mock_prov),
    ):
        result = runner.invoke(app, ["chat", "-m", "hi", "-p", "Ocean"])
    assert result.exit_code == 1
    assert "matches" in result.output


# ---------------------------------------------------------------------------
# cron
# ---------------------------------------------------------------------------


def test_cron_add_and_list_main(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    with patch("drclaw.cli.app._load_config", return_value=config):
        add = runner.invoke(
            app,
            ["cron", "add", "--message", "daily report", "--every", "60"],
        )
        ls = runner.invoke(app, ["cron", "list"])

    assert add.exit_code == 0
    assert "Added job" in add.output
    assert ls.exit_code == 0
    jobs = CronService(tmp_path / "cron" / "jobs.json").list_jobs(include_disabled=True)
    assert len(jobs) == 1
    assert jobs[0].name == "daily report"
    assert jobs[0].payload.target == "main"


def test_cron_add_target_project_by_name(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    store = JsonProjectStore(tmp_path)
    project = store.create_project("Alpha", "student project")

    with patch("drclaw.cli.app._load_config", return_value=config):
        result = runner.invoke(
            app,
            [
                "cron",
                "add",
                "--agent",
                "Alpha",
                "--message",
                "summarize yesterday",
                "--every",
                "120",
            ],
        )

    assert result.exit_code == 0
    service = CronService(tmp_path / "cron" / "jobs.json")
    jobs = service.list_jobs(include_disabled=True)
    assert len(jobs) == 1
    assert jobs[0].payload.target == f"proj:{project.id}"


def test_cron_add_requires_exactly_one_schedule(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    with patch("drclaw.cli.app._load_config", return_value=config):
        result = runner.invoke(
            app,
            [
                "cron",
                "add",
                "--message",
                "bad",
                "--every",
                "60",
                "--cron",
                "0 8 * * *",
            ],
        )
    assert result.exit_code == 1
    assert "Exactly one" in result.output


def test_cron_enable_disable_remove(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    with patch("drclaw.cli.app._load_config", return_value=config):
        add = runner.invoke(
            app,
            ["cron", "add", "--message", "toggle me", "--every", "60"],
        )
        assert add.exit_code == 0

        service = CronService(tmp_path / "cron" / "jobs.json")
        job_id = service.list_jobs(include_disabled=True)[0].id

        disable = runner.invoke(app, ["cron", "enable", job_id, "--disable"])
        enable = runner.invoke(app, ["cron", "enable", job_id])
        remove = runner.invoke(app, ["cron", "remove", job_id])

    assert disable.exit_code == 0
    assert "disabled" in disable.output
    assert enable.exit_code == 0
    assert "enabled" in enable.output
    assert remove.exit_code == 0
    assert "Removed job" in remove.output


def test_cron_run_executes_job(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    service = CronService(tmp_path / "cron" / "jobs.json")
    job = service.add_job(
        name="manual-run",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        target="main",
        message="run now",
    )

    with (
        patch("drclaw.cli.app._load_config", return_value=config),
        patch("drclaw.cli.app._make_provider", return_value=MockProvider()),
        patch(
            "drclaw.cli.app._execute_scheduled_job",
            new=AsyncMock(return_value="done"),
        ) as execute,
    ):
        result = runner.invoke(app, ["cron", "run", job.id])

    assert result.exit_code == 0
    assert "Job executed" in result.output
    execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# tray + launchd
# ---------------------------------------------------------------------------


def test_tray_requires_macos(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    with (
        patch("platform.system", return_value="Linux"),
        patch("drclaw.cli.app._load_config", return_value=config),
    ):
        result = runner.invoke(app, ["tray"])
    assert result.exit_code == 1
    assert "macOS-only" in result.output


def test_launchd_install_writes_plist_and_bootstraps(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.drclaw.tray.plist"
    with (
        patch("platform.system", return_value="Darwin"),
        patch("drclaw.cli.app._load_config", return_value=config),
        patch("drclaw.tray.launchd.write_launch_agent", return_value=plist_path) as write_agent,
        patch("drclaw.tray.launchd.launchctl") as launchctl,
    ):
        launchctl.side_effect = [
            subprocess.CompletedProcess(args=["launchctl"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["launchctl"], returncode=0, stdout="", stderr=""),
        ]
        result = runner.invoke(app, ["launchd", "install"])

    assert result.exit_code == 0
    assert "Installed LaunchAgent" in result.output
    write_agent.assert_called_once()
    assert launchctl.call_count == 2


def test_launchd_start_runs_kickstart(tmp_path: Path) -> None:
    config = DrClawConfig(data_dir=str(tmp_path))
    with (
        patch("platform.system", return_value="Darwin"),
        patch("drclaw.cli.app._load_config", return_value=config),
        patch(
            "drclaw.tray.launchd.launchctl",
            return_value=subprocess.CompletedProcess(
                args=["launchctl"], returncode=0, stdout="", stderr=""
            ),
        ) as launchctl,
    ):
        result = runner.invoke(app, ["launchd", "start"])

    assert result.exit_code == 0
    assert "Tray started" in result.output
    launchctl.assert_called_once()
