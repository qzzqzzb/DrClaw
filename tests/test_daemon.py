"""Tests for the daemon kernel, frontend adapter protocol, and daemon server."""

import asyncio
import signal
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from drclaw.bus.queue import MessageBus
from drclaw.config.schema import DrClawConfig, DaemonConfig
from drclaw.daemon.frontend import FrontendAdapter
from drclaw.daemon.kernel import Kernel
from drclaw.daemon.server import Daemon
from drclaw.models.messages import InboundMessage, OutboundMessage


@pytest.fixture()
def mock_provider():
    return MagicMock()


@pytest.fixture()
def config(tmp_data_dir: Path) -> DrClawConfig:
    return DrClawConfig(data_dir=str(tmp_data_dir))


class TestKernelBoot:
    def test_kernel_boot(self, config: DrClawConfig, mock_provider: MagicMock):
        with (
            patch("drclaw.daemon.kernel.AgentRegistry") as mock_registry_cls,
            patch("drclaw.daemon.kernel.JsonProjectStore") as mock_store_cls,
            patch("drclaw.daemon.kernel.CronService") as mock_cron_cls,
        ):
            mock_registry = mock_registry_cls.return_value
            mock_store = mock_store_cls.return_value
            mock_cron = mock_cron_cls.return_value

            kernel = Kernel.boot(config, mock_provider)

            assert kernel.bus is not None
            assert kernel.registry is mock_registry
            assert kernel.project_store is mock_store
            assert kernel.cron is mock_cron
            mock_registry.start_main.assert_called_once_with(debug_logger=None)
            mock_registry.spawn_activated_projects.assert_called_once_with(
                mock_store, debug_logger=None,
            )
            mock_cron_cls.assert_called_once_with(config.data_path / "cron" / "jobs.json")

    def test_kernel_boot_with_debug_logger(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ):
        logger = MagicMock()
        with (
            patch("drclaw.daemon.kernel.AgentRegistry") as mock_registry_cls,
            patch("drclaw.daemon.kernel.JsonProjectStore"),
            patch("drclaw.daemon.kernel.CronService"),
        ):
            mock_registry = mock_registry_cls.return_value

            Kernel.boot(config, mock_provider, debug_logger=logger)

            mock_registry.start_main.assert_called_once_with(debug_logger=logger)

    @pytest.mark.asyncio()
    async def test_kernel_boot_auto_activates_project_on_inbound(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ):
        with (
            patch("drclaw.daemon.kernel.AgentRegistry") as mock_registry_cls,
            patch("drclaw.daemon.kernel.JsonProjectStore") as mock_store_cls,
            patch("drclaw.daemon.kernel.CronService"),
        ):
            mock_registry = mock_registry_cls.return_value
            mock_store = mock_store_cls.return_value
            project = MagicMock()
            project.id = "demo-proj"
            mock_store.get_project.return_value = project

            kernel = Kernel.boot(config, mock_provider)
            await kernel.bus.publish_inbound(
                InboundMessage(channel="web", chat_id="c1", text="hello"),
                topic="proj:demo-proj",
            )

            mock_registry.activate_project.assert_called_once_with(
                project,
                interactive=True,
                debug_logger=None,
            )


class TestKernelShutdown:
    @pytest.mark.asyncio()
    async def test_kernel_shutdown(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ):
        with (
            patch("drclaw.daemon.kernel.AgentRegistry") as mock_registry_cls,
            patch("drclaw.daemon.kernel.JsonProjectStore"),
            patch("drclaw.daemon.kernel.CronService") as mock_cron_cls,
        ):
            mock_registry = mock_registry_cls.return_value
            mock_registry.stop_all = AsyncMock()
            mock_registry.claude_code_manager.stop_all = AsyncMock()
            mock_cron = mock_cron_cls.return_value

            kernel = Kernel.boot(config, mock_provider)
            await kernel.shutdown()

            mock_cron.stop.assert_called_once()
            mock_registry.stop_all.assert_awaited_once()


class TestFrontendAdapterProtocol:
    def test_frontend_adapter_protocol(self):
        class MyAdapter:
            adapter_id: str = "test"

            async def start(self, kernel: Kernel) -> None:
                pass

            async def stop(self) -> None:
                pass

        assert isinstance(MyAdapter(), FrontendAdapter)

    def test_frontend_adapter_protocol_missing_method(self):
        class Incomplete:
            adapter_id: str = "broken"

            async def start(self, kernel: Kernel) -> None:
                pass

            # missing stop()

        assert not isinstance(Incomplete(), FrontendAdapter)


class TestDaemon:
    def test_daemon_init(self, config: DrClawConfig, mock_provider: MagicMock):
        d = Daemon(config, mock_provider)
        assert d.config is config
        assert d.provider is mock_provider
        assert d._frontend_overrides is None
        assert d._debug_logger is None
        assert d._frontends == []

    @pytest.mark.asyncio()
    async def test_daemon_run_and_signal(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ):
        """Boot kernel, install signal handler, simulate SIGINT, verify shutdown."""
        mock_kernel = MagicMock()
        mock_kernel.shutdown = AsyncMock()
        mock_kernel.cron = MagicMock()
        mock_kernel.cron.start = AsyncMock()
        mock_kernel.cron.stop = MagicMock()
        mock_kernel.cron.status.return_value = {"jobs": 0}
        mock_kernel.registry.list_agents.return_value = []
        mock_kernel.bus = MessageBus()

        with patch("drclaw.daemon.server.Kernel") as mock_kernel_cls:
            mock_kernel_cls.boot.return_value = mock_kernel
            d = Daemon(config, mock_provider)

            # Schedule a SIGINT after a short delay so run() unblocks
            loop = asyncio.get_running_loop()
            loop.call_later(0.05, lambda: loop.call_soon(
                signal.raise_signal, signal.SIGINT,
            ))
            await d.run()

            mock_kernel_cls.boot.assert_called_once()
            mock_kernel.cron.start.assert_awaited_once()
            mock_kernel.shutdown.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_daemon_shutdown_order(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ):
        """Frontends stopped in reverse order, then kernel shutdown."""
        call_order: list[str] = []

        fe_a = MagicMock(spec=FrontendAdapter)
        fe_a.adapter_id = "a"
        fe_a.stop = AsyncMock(side_effect=lambda: call_order.append("stop_a"))

        fe_b = MagicMock(spec=FrontendAdapter)
        fe_b.adapter_id = "b"
        fe_b.stop = AsyncMock(side_effect=lambda: call_order.append("stop_b"))

        mock_kernel = MagicMock()
        mock_kernel.cron = MagicMock(
            stop=MagicMock(side_effect=lambda: call_order.append("cron_stop"))
        )
        mock_kernel.shutdown = AsyncMock(
            side_effect=lambda: call_order.append("kernel_shutdown"),
        )

        d = Daemon(config, mock_provider)
        d._kernel = mock_kernel
        d._frontends = [fe_a, fe_b]

        await d.shutdown()

        assert call_order == ["cron_stop", "stop_b", "stop_a", "kernel_shutdown"]

    def test_load_frontends_empty(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ):
        d = Daemon(config, mock_provider)
        assert d._load_frontends() == []

    def test_load_frontends_import(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ):
        fake_adapter = MagicMock()
        fake_adapter.adapter_id = "fake"
        fake_adapter.start = AsyncMock()
        fake_adapter.stop = AsyncMock()

        fake_module = types.ModuleType("drclaw.frontends.fake")
        # Adapter class that returns our fake_adapter instance
        fake_module.Adapter = lambda: fake_adapter  # type: ignore[attr-defined]

        with patch("importlib.import_module", return_value=fake_module):
            d = Daemon(config, mock_provider, frontend_overrides=["fake"])
            adapters = d._load_frontends()

        assert len(adapters) == 1

    def test_load_frontends_bad_module(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ):
        with patch(
            "importlib.import_module",
            side_effect=ImportError("no module named 'drclaw.frontends.nope'"),
        ):
            d = Daemon(config, mock_provider, frontend_overrides=["nope"])
            with pytest.raises(RuntimeError, match="Cannot load frontend 'nope'"):
                d._load_frontends()

    def test_load_frontends_missing_adapter_attr(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ):
        empty_module = types.ModuleType("drclaw.frontends.empty")
        with patch("importlib.import_module", return_value=empty_module):
            d = Daemon(config, mock_provider, frontend_overrides=["empty"])
            with pytest.raises(RuntimeError, match="has no 'Adapter' attribute"):
                d._load_frontends()

    def test_frontend_overrides(self, mock_provider: MagicMock):
        """CLI --frontend overrides config.daemon.frontends."""
        config = DrClawConfig(
            data_dir="/tmp/drclaw-test",
            daemon=DaemonConfig(frontends=["from_config"]),
        )
        d = Daemon(config, mock_provider, frontend_overrides=["from_cli"])
        # _load_frontends should use overrides, not config
        with patch(
            "importlib.import_module",
            side_effect=ImportError("expected"),
        ):
            with pytest.raises(RuntimeError, match="Cannot load frontend 'from_cli'"):
                d._load_frontends()

    def test_outbound_dest_formats_system_and_channel_targets(self) -> None:
        system_msg = OutboundMessage(
            channel="system",
            chat_id="proj:abc",
            text="x",
            topic="proj:abc",
        )
        feishu_msg = OutboundMessage(
            channel="feishu",
            chat_id="ou_123",
            text="x",
        )
        assert Daemon._outbound_dest(system_msg) == "proj:abc"
        assert Daemon._outbound_dest(feishu_msg) == "feishu:ou_123"

    def test_debug_listener_prints_student_tool_exec(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ) -> None:
        d = Daemon(config, mock_provider)
        with patch("drclaw.daemon.server.console.print") as mock_print:
            d._handle_debug_event(
                {
                    "type": "tool_exec",
                    "agent_id": "student:proj-1:researcher",
                    "tool_name": "web_fetch",
                    "result": "Fetched result body",
                }
            )

        mock_print.assert_called_once()
        printed = mock_print.call_args.args[0]
        assert "student:proj-1:researcher" in printed
        assert "web_fetch" in printed
        assert "Fetched result body" in printed

    def test_debug_listener_ignores_non_student_events(
        self, config: DrClawConfig, mock_provider: MagicMock,
    ) -> None:
        d = Daemon(config, mock_provider)
        with patch("drclaw.daemon.server.console.print") as mock_print:
            d._handle_debug_event(
                {
                    "type": "tool_exec",
                    "agent_id": "proj:demo",
                    "tool_name": "web_fetch",
                    "result": "Fetched result body",
                }
            )

        mock_print.assert_not_called()


class TestDaemonCLI:
    def test_daemon_command_exists(self):
        from drclaw.cli.app import app

        # Typer registers commands with their function name
        cmd_names = [cmd.name or cmd.callback.__name__ for cmd in app.registered_commands]
        assert "daemon" in cmd_names
