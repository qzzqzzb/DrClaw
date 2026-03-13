"""Interactive REPL — event-driven multiplexed display with @mention routing."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from drclaw.agent.registry import AgentRegistry
from drclaw.bus.queue import MessageBus
from drclaw.models.messages import InboundMessage

if TYPE_CHECKING:
    from drclaw.agent.debug import DebugLogger
    from drclaw.models.project import ProjectStore

EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

console = Console()


def _is_exit_command(cmd: str) -> bool:
    """Return True when *cmd* should end the interactive session."""
    return cmd.lower() in EXIT_COMMANDS


def _parse_mention(text: str) -> tuple[str | None, str]:
    """Parse ``@student_name`` from the start of *text*.

    Returns ``(project_name, rest_of_message)`` or ``(None, text)``
    if there is no leading mention.
    """
    stripped = text.strip()
    if not stripped.startswith("@"):
        return None, text
    parts = stripped.split(None, 1)
    name = parts[0][1:]  # strip leading @
    if not name:
        return None, text
    rest = parts[1] if len(parts) > 1 else ""
    return name, rest


def _save_terminal() -> list | None:
    """Snapshot termios state so we can restore it on exit."""
    try:
        import termios

        return termios.tcgetattr(sys.stdin.fileno())
    except (ImportError, OSError):
        return None


def _restore_terminal(saved: list | None) -> None:
    """Restore terminal to its original state."""
    if saved is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
    except (ImportError, OSError):
        pass


def _flush_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except (ValueError, OSError):
        return
    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
    except (ImportError, OSError):
        pass


def _make_tool_cb(con: Console, label: str) -> Callable[[str, dict[str, Any]], None]:
    """Build a tool-call display callback for a specific agent."""
    def cb(name: str, args: dict[str, Any]) -> None:
        raw = str(args)
        summary = raw[:80] + "..." if len(raw) > 80 else raw
        con.print(f"[dim]  \\[{name}]({summary})[/dim]")
    return cb


async def _display_loop(bus: MessageBus, registry: AgentRegistry, con: Console) -> None:
    """Continuously render outbound messages from any agent."""
    try:
        while True:
            msg = await bus.consume_outbound()
            if not msg.text:
                continue
            handle = registry.get(msg.source)
            label = handle.label if handle else msg.source
            con.print()
            con.print(f"[bold]{label}>[/bold]")
            con.print(Markdown(msg.text))
            con.print()
    except asyncio.CancelledError:
        return


async def _parse_target(
    text: str,
    registry: AgentRegistry,
    project_store: ProjectStore,
    *,
    debug_logger: DebugLogger | None = None,
) -> tuple[str | None, str]:
    """Resolve user input to ``(agent_id, message_text)``.

    Returns ``(None, error_msg)`` when the mention can't be resolved.
    """
    mention, rest = _parse_mention(text)
    if mention is None:
        return "main", text

    # Check running agents first (by label, case-insensitive)
    handle = registry.find_by_project_name(mention)
    if handle is not None:
        return handle.agent_id, rest

    # Try project store — spawn if found
    from drclaw.models.project import AmbiguousProjectNameError

    try:
        project = project_store.find_by_name(mention)
    except AmbiguousProjectNameError as exc:
        return None, str(exc)
    if project is None:
        project = project_store.get_project(mention)
    if project is None:
        return None, f"Student/project '{mention}' not found"

    handle = registry.spawn_project(project, interactive=True, debug_logger=debug_logger)
    session_key = f"proj:{project.id}"
    await handle.loop.consolidate_on_startup(session_key)
    handle.loop.on_tool_call = _make_tool_cb(console, handle.label)
    return handle.agent_id, rest


def _wire_tool_callbacks(registry: AgentRegistry, con: Console) -> None:
    """Set tool-call display callbacks on all existing agent handles."""
    for handle in registry.list_agents():
        handle.loop.on_tool_call = _make_tool_cb(con, handle.label)


async def _handle_command(
    cmd: str, registry: AgentRegistry, con: Console,
) -> bool:
    """Process slash commands. Returns True if handled."""
    parts = cmd.split()
    command = parts[0].lower()

    if command == "/agents":
        table = Table(title="Running Agents")
        table.add_column("ID")
        table.add_column("Label")
        table.add_column("Type")
        table.add_column("Status")
        for h in registry.list_agents():
            table.add_row(h.agent_id, h.label, h.agent_type, h.status.value)
        con.print(table)
        return True

    if command == "/stop":
        if len(parts) < 2:
            con.print("[yellow]Usage:[/yellow] /stop <agent_name>")
            return True
        name = parts[1]
        if name.lower() == "main":
            con.print("[red]Error:[/red] Cannot stop the Assistant Agent")
            return True
        handle = registry.find_by_project_name(name)
        if handle is None:
            # Fall back to agent ID (shown in /agents output)
            handle = registry.get(name)
        if handle is None:
            con.print(f"[red]Error:[/red] No running agent named '{name}'")
            return True
        stopped = await registry.stop(handle.agent_id)
        if stopped:
            con.print(f"Stopped agent '{handle.label}'")
        else:
            con.print(f"[yellow]Agent '{handle.label}' already stopped[/yellow]")
        return True

    return False


async def run_repl(
    registry: AgentRegistry,
    bus: MessageBus,
    project_store: ProjectStore,
    data_dir: Path | None = None,
    *,
    debug_logger: DebugLogger | None = None,
) -> None:
    """Run the multiplexed interactive REPL loop.

    Agents are managed by *registry*; responses arrive asynchronously
    via the shared *bus* outbound queue. ``@student_name`` mentions
    route messages to the appropriate student agent.
    """
    saved_term = _save_terminal()

    history_dir = data_dir
    if history_dir is None:
        store_data_dir = getattr(project_store, "_data_dir", None)
        if isinstance(store_data_dir, Path):
            history_dir = store_data_dir
        else:
            history_dir = Path(os.path.expanduser("~/.drclaw"))
    history_path = history_dir / "history.txt"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_path)),
        multiline=False,
    )

    _wire_tool_callbacks(registry, console)

    display_task = asyncio.create_task(_display_loop(bus, registry, console))

    console.print(
        "[bold]DrClaw[/bold] — type your message. "
        "Use @student to route. /agents to list. exit/quit/Ctrl+C to leave.\n"
    )

    try:
        while True:
            try:
                _flush_input()
                with patch_stdout():
                    user_input = await prompt_session.prompt_async(
                        HTML("<cyan>You&gt; </cyan>")
                    )

                cmd = user_input.strip()
                if not cmd:
                    continue
                if _is_exit_command(cmd):
                    break

                # Slash commands
                if cmd.startswith("/"):
                    handled = await _handle_command(cmd, registry, console)
                    if handled:
                        continue

                agent_id, message_text = await _parse_target(
                    cmd, registry, project_store, debug_logger=debug_logger,
                )
                if agent_id is None:
                    console.print(f"[red]Error:[/red] {message_text}")
                    continue

                # Determine chat_id from agent_id
                if agent_id == "main":
                    chat_id = "main"
                else:
                    # agent_id is "proj:{id}", extract project id
                    chat_id = f"proj-{agent_id.split(':', 1)[1]}"

                await bus.publish_inbound(
                    InboundMessage(channel="cli", chat_id=chat_id, text=message_text),
                    topic=agent_id,
                )
            except (KeyboardInterrupt, EOFError):
                break
    finally:
        console.print("\nGoodbye!")
        _restore_terminal(saved_term)
        await registry.stop_all()
        display_task.cancel()
        try:
            await display_task
        except asyncio.CancelledError:
            pass
