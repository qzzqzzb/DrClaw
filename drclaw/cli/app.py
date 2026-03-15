"""DrClaw CLI — typer application entry point."""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from drclaw.agent.debug import DebugLogger

from rich.console import Console
from rich.table import Table

from drclaw import __version__
from drclaw.config.loader import load_config, save_config
from drclaw.config.schema import DrClawConfig
from drclaw.models.project import (
    AmbiguousProjectNameError,
    CorruptProjectStoreError,
    JsonProjectStore,
    Project,
)
from drclaw.providers.base import LLMProvider
from drclaw.utils.helpers import ensure_default_skill_dirs, get_data_dir

app = typer.Typer(name="drclaw", help="DrClaw — research lab agent framework.")
projects_app = typer.Typer(help="Manage research projects.")
cron_app = typer.Typer(help="Manage scheduled cron jobs.")
launchd_app = typer.Typer(help="Manage macOS LaunchAgent for tray mode.")
app.add_typer(projects_app, name="projects")
app.add_typer(cron_app, name="cron")
app.add_typer(launchd_app, name="launchd")

console = Console()


def _load_config() -> DrClawConfig:
    """Load config from the default data directory."""
    data_dir = get_data_dir()
    return load_config(data_dir / "config.json")


def _ensure_macos_or_exit() -> None:
    if platform.system() != "Darwin":
        console.print("[red]Error:[/red] launchd/tray commands are macOS-only")
        raise typer.Exit(code=1)


def _make_provider(config: DrClawConfig):  # noqa: ANN201
    """Build a LiteLLMProvider from config. Lazy import to keep lightweight commands fast."""
    from drclaw.providers.litellm_provider import LiteLLMProvider

    return LiteLLMProvider(
        config.active_provider_config,
        max_tokens=config.agent.max_tokens,
        temperature=config.agent.temperature,
    )


def _remove_agent_memory_state(data_dir: Path) -> tuple[int, int]:
    """Remove agent memory artifacts only.

    Deletes:
    - session directories for main/project/equipment agents
    - consolidated memory files (MEMORY.md/HISTORY.md) for main/project agents

    Keeps:
    - config, skills, local skill hub, SOUL/PROJECT/workspace files, project registry
    """
    removed_sessions = 0
    removed_memory_files = 0

    session_dirs: list[Path] = [data_dir / "sessions"]
    projects_dir = data_dir / "projects"
    if projects_dir.exists():
        session_dirs.extend(p for p in projects_dir.glob("*/sessions"))

    runtime_dir = data_dir / "runtime"
    if runtime_dir.exists():
        session_dirs.extend(p for p in runtime_dir.rglob("sessions"))

    # Remove duplicates while preserving order.
    seen: set[Path] = set()
    for session_dir in session_dirs:
        if session_dir in seen:
            continue
        seen.add(session_dir)
        if session_dir.is_dir():
            shutil.rmtree(session_dir)
            removed_sessions += 1

    memory_files: list[Path] = [data_dir / "MEMORY.md", data_dir / "HISTORY.md"]
    if projects_dir.exists():
        for project_dir in projects_dir.glob("*"):
            if not project_dir.is_dir():
                continue
            memory_files.append(project_dir / "MEMORY.md")
            memory_files.append(project_dir / "HISTORY.md")

    for file_path in memory_files:
        if file_path.exists():
            file_path.unlink()
            removed_memory_files += 1

    return removed_sessions, removed_memory_files


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(f"DrClaw v{__version__} — run 'drclaw --help' for usage.")


@app.command()
def onboard() -> None:
    """Initialize DrClaw — create ~/.drclaw/ and default config."""
    data_dir = get_data_dir()
    ensure_default_skill_dirs(data_dir)
    config_path = data_dir / "config.json"
    if config_path.exists():
        console.print(f"Already initialized at {data_dir}")
    else:
        config = DrClawConfig(data_dir=str(data_dir))
        save_config(config, config_path)
        console.print(f"Initialized DrClaw at {data_dir}")


@app.command()
def status() -> None:
    """Show DrClaw status — version, data dir, model, project count."""
    config = _load_config()
    store = JsonProjectStore(config.data_path)
    try:
        count = len(store.list_projects())
    except CorruptProjectStoreError:
        console.print("[red]Error:[/red] projects.json is corrupted")
        raise typer.Exit(code=1) from None
    console.print(f"DrClaw v{__version__}")
    console.print(f"Data dir: {config.data_path}")
    console.print(f"Model:    {config.active_provider_config.model} [{config.active_provider}]")
    console.print(f"Projects: {count}")


@app.command()
def reset(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm destructive reset of local DrClaw state",
    ),
    reset_config: bool = typer.Option(
        False,
        "--reset-config",
        help="Also reset config.json to defaults",
    ),
    memory_only: bool = typer.Option(
        False,
        "--memory-only",
        help="Only clear agent memory (sessions + MEMORY/HISTORY), keep projects/config/skills",
    ),
) -> None:
    """Reset local DrClaw state to a clean start."""
    if not yes:
        console.print("[red]Error:[/red] reset is destructive. Re-run with --yes to continue.")
        raise typer.Exit(code=1)

    if reset_config and memory_only:
        console.print("[red]Error:[/red] --reset-config cannot be used with --memory-only.")
        raise typer.Exit(code=1)

    data_dir = get_data_dir()

    if memory_only:
        removed_sessions, removed_memory_files = _remove_agent_memory_state(data_dir)
        console.print(
            "Reset agent memory at "
            f"{data_dir} (removed {removed_sessions} session dirs, "
            f"{removed_memory_files} consolidated memory files)"
        )
        return

    config_path = data_dir / "config.json"
    preserved_config: bytes | None = None
    if not reset_config and config_path.exists():
        preserved_config = config_path.read_bytes()

    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    if preserved_config is not None:
        config_path.write_bytes(preserved_config)
        ensure_default_skill_dirs(data_dir)
        console.print(f"Reset DrClaw state at {data_dir} (kept config.json)")
    else:
        save_config(DrClawConfig(data_dir=str(data_dir)), config_path)
        ensure_default_skill_dirs(data_dir)
        console.print(f"Reset DrClaw state at {data_dir} (default config.json created)")


@projects_app.command("list")
def projects_list() -> None:
    """List all research projects."""
    config = _load_config()
    store = JsonProjectStore(config.data_path)
    try:
        projects = store.list_projects()
    except CorruptProjectStoreError:
        console.print("[red]Error:[/red] projects.json is corrupted")
        raise typer.Exit(code=1) from None
    if not projects:
        console.print("No projects yet. Create one with: drclaw projects create <name>")
        return
    table = Table(title="Projects")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Description")
    for p in projects:
        table.add_row(p.id, p.name, p.status, p.description)
    console.print(table)


@projects_app.command("create")
def projects_create(
    name: str = typer.Argument(..., help="Project name"),
    description: str = typer.Option("", "-d", "--description", help="Project description"),
) -> None:
    """Create a new research project."""
    config = _load_config()
    store = JsonProjectStore(config.data_path)
    try:
        project = store.create_project(name, description)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from None
    console.print(f"Created project '{project.name}' ({project.id})")


def _make_debug_logger(
    data_path: Path, session_key: str, *, full: bool = False
) -> tuple[Path, DebugLogger]:
    """Create a DebugLogger and return (log_path, logger)."""
    from drclaw.agent.debug import make_debug_logger

    return make_debug_logger(data_path / "debug", session_key, full=full)


@app.command()
def chat(
    message: str | None = typer.Option(None, "-m", "--message", help="Single message to send"),
    project: str | None = typer.Option(None, "-p", "--project", help="Project name or ID"),
    debug: bool = typer.Option(False, "--debug", help="Log LLM interactions to a debug file"),
    debug_full: bool = typer.Option(
        False, "--debug-full", help="Log full LLM message arrays (implies --debug)"
    ),
) -> None:
    """Chat with DrClaw — send a single message or start interactive mode."""
    config = _load_config()
    provider = _make_provider(config)

    if debug_full:
        debug = True

    dl = None
    session_key = f"proj:{project}" if project else "main"
    if debug:
        log_path, dl = _make_debug_logger(config.data_path, session_key, full=debug_full)
        console.print(f"Debug log: {log_path}")
        dl.log_session_start(session_key, config.active_provider_config.model)

    try:
        if message is None:
            if project is not None:
                console.print(
                    "[red]Error:[/red] -p without -m is not supported. "
                    "Use @mention in interactive mode."
                )
                raise typer.Exit(code=1)
            asyncio.run(_run_interactive(config, provider, debug_logger=dl))
        elif project is not None:
            _chat_project(config, provider, project, message, debug_logger=dl)
        else:
            _chat_main(config, provider, message, debug_logger=dl)
    finally:
        if dl:
            dl.close()


@app.command()
def daemon(
    frontend: list[str] | None = typer.Option(
        None, "--frontend", "-f", help="Frontend adapters to load (overrides config)",
    ),
    debug: bool = typer.Option(False, "--debug", help="Log LLM interactions to a debug file"),
    debug_full: bool = typer.Option(
        False, "--debug-full", help="Log full LLM message arrays (implies --debug)",
    ),
) -> None:
    """Start DrClaw in daemon mode — boot kernel, load frontends, block until signal."""
    from drclaw.daemon.server import Daemon

    config = _load_config()
    provider = _make_provider(config)

    if debug_full:
        debug = True

    dl = None
    if debug:
        log_path, dl = _make_debug_logger(config.data_path, "daemon", full=debug_full)
        console.print(f"Debug log: {log_path}")

    try:
        d = Daemon(config, provider, frontend_overrides=frontend, debug_logger=dl)
        asyncio.run(d.run())
    finally:
        if dl:
            dl.close()


@app.command()
def tray() -> None:
    """Run menu-bar tray and manage daemon lifecycle."""
    _ensure_macos_or_exit()
    from drclaw.tray import run_tray

    config = _load_config()
    run_tray(config)


def _launchd_log_paths(config: DrClawConfig) -> tuple[Path, Path]:
    logs_dir = config.data_path / "logs"
    return logs_dir / "launchd-tray.out.log", logs_dir / "launchd-tray.err.log"


@launchd_app.command("install")
def launchd_install(
    label: str = typer.Option("com.drclaw.tray", "--label", help="LaunchAgent label"),
    run_at_load: bool = typer.Option(
        False, "--run-at-load", help="Start tray automatically when user logs in",
    ),
) -> None:
    """Install and bootstrap tray LaunchAgent plist."""
    _ensure_macos_or_exit()
    from drclaw.tray.launchd import launchctl, launchctl_target, write_launch_agent

    config = _load_config()
    stdout_path, stderr_path = _launchd_log_paths(config)
    plist_path = write_launch_agent(
        label=label,
        run_at_load=run_at_load,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    target = launchctl_target(label)
    with suppress(Exception):
        launchctl("bootout", target)
    result = launchctl("bootstrap", f"gui/{os.getuid()}", str(plist_path))
    if result.returncode != 0:
        console.print(f"[red]Error:[/red] launchctl bootstrap failed\n{result.stderr.strip()}")
        raise typer.Exit(code=1)

    console.print(f"[green]✓[/green] Installed LaunchAgent at {plist_path}")
    if run_at_load:
        start = launchctl("kickstart", "-k", target)
        if start.returncode != 0:
            console.print(f"[yellow]Warning:[/yellow] kickstart failed\n{start.stderr.strip()}")
        else:
            console.print("[green]✓[/green] Tray started")


@launchd_app.command("start")
def launchd_start(
    label: str = typer.Option("com.drclaw.tray", "--label", help="LaunchAgent label"),
) -> None:
    """Start tray via launchctl kickstart."""
    _ensure_macos_or_exit()
    from drclaw.tray.launchd import launchctl, launchctl_target

    target = launchctl_target(label)
    result = launchctl("kickstart", "-k", target)
    if result.returncode != 0:
        console.print(f"[red]Error:[/red] launchctl kickstart failed\n{result.stderr.strip()}")
        raise typer.Exit(code=1)
    console.print("[green]✓[/green] Tray started")


@launchd_app.command("stop")
def launchd_stop(
    label: str = typer.Option("com.drclaw.tray", "--label", help="LaunchAgent label"),
) -> None:
    """Stop tray via launchctl bootout."""
    _ensure_macos_or_exit()
    from drclaw.tray.launchd import launchctl, launchctl_target

    target = launchctl_target(label)
    result = launchctl("bootout", target)
    if result.returncode != 0:
        console.print(f"[red]Error:[/red] launchctl bootout failed\n{result.stderr.strip()}")
        raise typer.Exit(code=1)
    console.print("[green]✓[/green] Tray stopped")


@launchd_app.command("status")
def launchd_status(
    label: str = typer.Option("com.drclaw.tray", "--label", help="LaunchAgent label"),
) -> None:
    """Show launchctl status for tray LaunchAgent."""
    _ensure_macos_or_exit()
    from drclaw.tray.launchd import launchctl, launchctl_target

    target = launchctl_target(label)
    result = launchctl("print", target)
    if result.returncode != 0:
        console.print(f"[yellow]Not loaded:[/yellow] {label}")
        if result.stderr.strip():
            console.print(result.stderr.strip())
        raise typer.Exit(code=1)
    console.print(result.stdout.strip())


@launchd_app.command("uninstall")
def launchd_uninstall(
    label: str = typer.Option("com.drclaw.tray", "--label", help="LaunchAgent label"),
) -> None:
    """Unload and remove tray LaunchAgent plist."""
    _ensure_macos_or_exit()
    from drclaw.tray.launchd import default_launch_agent_path, launchctl, launchctl_target

    target = launchctl_target(label)
    with suppress(Exception):
        launchctl("bootout", target)

    plist_path = default_launch_agent_path(label)
    if plist_path.exists():
        plist_path.unlink()
        console.print(f"[green]✓[/green] Removed {plist_path}")
    else:
        console.print(f"[yellow]No plist found:[/yellow] {plist_path}")


def _cron_store_path(config: DrClawConfig) -> Path:
    return config.data_path / "cron" / "jobs.json"


def _resolve_cron_target_or_exit(config: DrClawConfig, target_ref: str) -> tuple[str, str]:
    ref = target_ref.strip()
    if not ref:
        console.print("[red]Error:[/red] --agent cannot be empty")
        raise typer.Exit(code=1)
    if ref.lower() == "main":
        return "main", "Assistant Agent"
    if ref.startswith("proj:"):
        ref = ref.split(":", 1)[1]

    project = _resolve_project_or_exit(config, ref)
    return f"proj:{project.id}", project.name


def _build_schedule_or_exit(
    *,
    every: int | None,
    cron_expr: str | None,
    tz: str | None,
    at: str | None,
):
    from drclaw.cron.types import CronSchedule

    choices = [every is not None, bool(cron_expr), bool(at)]
    if sum(1 for c in choices if c) != 1:
        console.print("[red]Error:[/red] Exactly one of --every, --cron, or --at must be set")
        raise typer.Exit(code=1)
    if tz and not cron_expr:
        console.print("[red]Error:[/red] --tz can only be used with --cron")
        raise typer.Exit(code=1)
    if every is not None:
        if every <= 0:
            console.print("[red]Error:[/red] --every must be > 0")
            raise typer.Exit(code=1)
        return CronSchedule(kind="every", every_ms=every * 1000)
    if cron_expr:
        return CronSchedule(kind="cron", expr=cron_expr, tz=tz)
    try:
        dt = datetime.fromisoformat(at or "")
    except ValueError:
        console.print("[red]Error:[/red] --at must be ISO datetime, e.g. 2026-03-08T08:00:00")
        raise typer.Exit(code=1) from None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))


def _format_ts(ms: int | None, tz_name: str | None = None) -> str:
    if ms is None:
        return ""
    ts = ms / 1000
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return datetime.fromtimestamp(ts, tz=ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _format_schedule(job) -> str:  # noqa: ANN001
    if job.schedule.kind == "every":
        return f"every {(job.schedule.every_ms or 0) // 1000}s"
    if job.schedule.kind == "cron":
        expr = job.schedule.expr or ""
        return f"{expr} ({job.schedule.tz})" if job.schedule.tz else expr
    return "one-time"


async def _execute_scheduled_job(config: DrClawConfig, provider: LLMProvider, job) -> str:  # noqa: ANN001
    target = job.payload.target
    if target == "main":
        from drclaw.agent.main_agent import MainAgent

        agent = MainAgent(config, provider)
        return await agent.process_direct(job.payload.message)

    if target.startswith("proj:"):
        from drclaw.agent.project_agent import ProjectAgent

        project_id = target.split(":", 1)[1]
        project = JsonProjectStore(config.data_path).get_project(project_id)
        if project is None:
            raise RuntimeError(f"project not found: {project_id}")
        agent = ProjectAgent(config, provider, project)
        return await agent.process_direct(job.payload.message)

    raise RuntimeError(f"unsupported cron target {target!r}")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
) -> None:
    """List scheduled jobs."""
    from drclaw.cron.service import CronService

    config = _load_config()
    service = CronService(_cron_store_path(config))
    jobs = service.list_jobs(include_disabled=all)
    if not jobs:
        console.print("No scheduled jobs.")
        return

    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Target")
    table.add_column("Schedule")
    table.add_column("Delivery")
    table.add_column("Status")
    table.add_column("Next Run")
    table.add_column("Last")

    for job in jobs:
        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"
        delivery = f"{job.payload.channel}:{job.payload.chat_id}"
        table.add_row(
            job.id,
            job.name,
            job.payload.target,
            _format_schedule(job),
            delivery,
            status,
            _format_ts(job.state.next_run_at_ms, job.schedule.tz),
            job.state.last_status or "",
        )

    console.print(table)


@cron_app.command("add")
def cron_add(
    message: str = typer.Option(..., "--message", "-m", help="Prompt sent to the target agent"),
    agent: str = typer.Option(
        "main", "--agent", "-a", help="Target agent: 'main', project ID/name, or proj:<id>",
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Job name"),
    every: int | None = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str | None = typer.Option(
        None, "--cron", "-c", help="Cron expression (e.g. '0 8 * * *')",
    ),
    tz: str | None = typer.Option(None, "--tz", help="IANA timezone for cron expressions"),
    at: str | None = typer.Option(None, "--at", help="One-time ISO datetime"),
    channel: str = typer.Option("system", "--channel", help="Outbound channel for response"),
    chat_id: str | None = typer.Option(None, "--chat-id", help="Outbound chat/user id"),
) -> None:
    """Create a scheduled job."""
    from drclaw.cron.service import CronService

    config = _load_config()
    target, label = _resolve_cron_target_or_exit(config, agent)
    schedule = _build_schedule_or_exit(every=every, cron_expr=cron_expr, tz=tz, at=at)
    service = CronService(_cron_store_path(config))

    job_name = name or message.strip()[:40] or f"job-{target}"
    to_chat_id = (chat_id or target).strip() or target

    try:
        job = service.add_job(
            name=job_name,
            schedule=schedule,
            target=target,
            message=message,
            channel=channel,
            chat_id=to_chat_id,
            delete_after_run=(schedule.kind == "at"),
        )
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from None

    console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id}) → {label} [{target}]")
    if job.state.next_run_at_ms:
        console.print(f"Next run: {_format_ts(job.state.next_run_at_ms, job.schedule.tz)}")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
) -> None:
    """Remove a scheduled job."""
    from drclaw.cron.service import CronService

    config = _load_config()
    service = CronService(_cron_store_path(config))
    if service.remove_job(job_id):
        console.print(f"[green]✓[/green] Removed job {job_id}")
        return
    console.print(f"[red]Error:[/red] Job {job_id} not found")
    raise typer.Exit(code=1)


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
) -> None:
    """Enable or disable a job."""
    from drclaw.cron.service import CronService

    config = _load_config()
    service = CronService(_cron_store_path(config))
    job = service.enable_job(job_id, enabled=not disable)
    if job is None:
        console.print(f"[red]Error:[/red] Job {job_id} not found")
        raise typer.Exit(code=1)
    action = "disabled" if disable else "enabled"
    console.print(f"[green]✓[/green] Job '{job.name}' {action}")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to execute now"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
) -> None:
    """Run a scheduled job immediately."""
    from drclaw.cron.service import CronService

    config = _load_config()
    provider = _make_provider(config)
    service = CronService(_cron_store_path(config))
    results: list[str] = []

    async def _on_job(job) -> str | None:  # noqa: ANN001
        result = await _execute_scheduled_job(config, provider, job)
        results.append(result)
        return result

    service.on_job = _on_job
    ok = asyncio.run(service.run_job(job_id, force=force))
    if not ok:
        console.print(f"[red]Error:[/red] Job {job_id} not found or disabled")
        raise typer.Exit(code=1)

    console.print("[green]✓[/green] Job executed")
    if results:
        console.print(results[0])


def _find_project(config: DrClawConfig, project_ref: str) -> Project | None:
    """Look up a project by name or ID. Returns None if not found.

    Raises AmbiguousProjectNameError if the reference matches multiple projects.
    """
    store = JsonProjectStore(config.data_path)
    proj = store.find_by_name(project_ref)
    if proj is None:
        proj = store.get_project(project_ref)
    return proj


def _resolve_project_or_exit(config: DrClawConfig, project_ref: str) -> Project:
    """Look up a project by name or ID, or print an error and exit."""
    try:
        proj = _find_project(config, project_ref)
    except AmbiguousProjectNameError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from None
    if proj is None:
        console.print(f"[red]Error:[/red] Project '{project_ref}' not found")
        raise typer.Exit(code=1) from None
    return proj


async def _run_interactive(
    config: DrClawConfig,
    provider: LLMProvider,
    debug_logger: DebugLogger | None = None,
) -> None:
    from drclaw.agent.registry import AgentRegistry
    from drclaw.bus.queue import MessageBus
    from drclaw.cli.repl import run_repl

    bus = MessageBus()
    project_store = JsonProjectStore(config.data_path)
    registry = AgentRegistry(config, provider, bus)

    main_handle = registry.start_main(debug_logger=debug_logger)
    await main_handle.loop.consolidate_on_startup("main")

    await run_repl(
        registry, bus, project_store,
        data_dir=config.data_path, debug_logger=debug_logger,
    )


def _chat_main(
    config: DrClawConfig,
    provider: LLMProvider,
    message: str,
    debug_logger: DebugLogger | None = None,
) -> None:
    from drclaw.agent.main_agent import MainAgent

    agent = MainAgent(config, provider, debug_logger=debug_logger)
    result = asyncio.run(agent.process_direct(message))
    console.print(result)


def _chat_project(
    config: DrClawConfig,
    provider: LLMProvider,
    project_ref: str,
    message: str,
    debug_logger: DebugLogger | None = None,
) -> None:
    from drclaw.agent.project_agent import ProjectAgent

    proj = _resolve_project_or_exit(config, project_ref)
    agent = ProjectAgent(config, provider, proj, debug_logger=debug_logger)
    result = asyncio.run(agent.process_direct(message))
    console.print(result)
