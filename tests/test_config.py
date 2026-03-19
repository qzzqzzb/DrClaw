"""Tests for config schema and loader."""

from pathlib import Path

import pytest

from drclaw.config.loader import ConfigLoadError, load_config, load_config_strict, save_config
from drclaw.config.schema import (
    AcpxConfig,
    AgentConfig,
    DaemonConfig,
    DrClawConfig,
    EnvConfig,
    ExternalAgentConfig,
    FeishuConfig,
    ProviderConfig,
    SerperConfig,
    ToolsConfig,
    TrayConfig,
    WebToolsConfig,
)


def test_config_defaults():
    cfg = DrClawConfig()
    assert cfg.data_dir == "~/.drclaw"
    assert cfg.providers == {"default": ProviderConfig()}
    assert cfg.active_provider == "default"
    assert cfg.active_provider_config.model == "anthropic/claude-sonnet-4-5"
    assert cfg.active_provider_config.api_key == ""
    assert cfg.active_provider_config.api_base is None
    assert cfg.agent.max_iterations == 40
    assert cfg.agent.max_tokens == 8192
    assert cfg.agent.temperature == 0.1
    assert cfg.agent.memory_window == 100
    assert cfg.agent.tool_detach_timeout_seconds == 60
    assert cfg.feishu.app_id == ""
    assert cfg.feishu.app_secret == ""
    assert cfg.feishu.allow_from == []
    assert cfg.feishu.reconnect_interval_seconds == 5
    assert cfg.tray.control_panel_url == "http://127.0.0.1:8080"
    assert cfg.tray.daemon_program[:4] == ["uv", "run", "drclaw", "daemon"]
    assert "NO_PROXY" in cfg.tray.daemon_env
    assert cfg.tray.shutdown_timeout_seconds == 8
    assert cfg.tools.web.serper.api_key == ""
    assert cfg.tools.web.serper.endpoint == "https://google.serper.dev/search"
    assert cfg.tools.web.serper.max_results == 5
    assert cfg.claude_code.enabled is False
    assert cfg.acpx == AcpxConfig()
    assert cfg.env.global_ == {}
    assert cfg.env.scoped == {}
    assert cfg.external_agents == []


def test_config_data_path_expands_tilde():
    cfg = DrClawConfig()
    assert "~" not in str(cfg.data_path)
    assert cfg.data_path.is_absolute()


def test_config_save_load_roundtrip(tmp_data_dir: Path):
    path = tmp_data_dir / "config.json"
    cfg = DrClawConfig(
        providers={"default": ProviderConfig(api_key="sk-test", model="openai/gpt-4o")},
        active_provider="default",
        agent=AgentConfig(
            max_iterations=20,
            temperature=0.5,
            tool_detach_timeout_seconds=30,
        ),
        acpx=AcpxConfig(enabled=True, command="acpx", default_agent="codex"),
        data_dir="/tmp/drclaw",
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.active_provider_config.api_key == "sk-test"
    assert loaded.active_provider_config.model == "openai/gpt-4o"
    assert loaded.agent.max_iterations == 20
    assert loaded.agent.temperature == 0.5
    assert loaded.agent.tool_detach_timeout_seconds == 30
    assert loaded.acpx.enabled is True
    assert loaded.acpx.command == "acpx"
    assert loaded.acpx.default_agent == "codex"
    assert loaded.data_dir == "/tmp/drclaw"


def test_config_missing_file_returns_defaults(tmp_data_dir: Path):
    path = tmp_data_dir / "nonexistent.json"
    cfg = load_config(path)
    assert cfg == DrClawConfig()


def test_multi_provider_roundtrip(tmp_data_dir: Path):
    path = tmp_data_dir / "config.json"
    cfg = DrClawConfig(
        providers={
            "anthropic": ProviderConfig(api_key="sk-ant", model="anthropic/claude-sonnet-4-5"),
            "openai": ProviderConfig(api_key="sk-oai", model="openai/gpt-4o"),
        },
        active_provider="openai",
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.active_provider == "openai"
    assert loaded.active_provider_config.model == "openai/gpt-4o"
    assert loaded.providers["anthropic"].api_key == "sk-ant"
    assert loaded.providers["openai"].api_key == "sk-oai"


def test_active_provider_config_raises_when_missing():
    cfg = DrClawConfig(
        providers={"default": ProviderConfig()},
        active_provider="nonexistent",
    )
    import pytest
    with pytest.raises(ValueError, match="nonexistent"):
        _ = cfg.active_provider_config


def test_migrate_legacy_provider_key(tmp_data_dir: Path):
    path = tmp_data_dir / "config.json"
    path.write_text(
        '{"provider": {"api_key": "sk-legacy", "model": "anthropic/claude-sonnet-4-5"}}',
        encoding="utf-8",
    )
    loaded = load_config(path)
    assert loaded.active_provider == "default"
    assert loaded.providers["default"].api_key == "sk-legacy"
    assert loaded.providers["default"].model == "anthropic/claude-sonnet-4-5"


def test_model_accepts_legacy_provider_key():
    cfg = DrClawConfig.model_validate(
        {"provider": {"api_key": "sk-legacy", "model": "openai/gpt-4o"}}
    )
    assert cfg.active_provider == "default"
    assert cfg.active_provider_config.api_key == "sk-legacy"
    assert cfg.active_provider_config.model == "openai/gpt-4o"
    assert cfg.provider.model == "openai/gpt-4o"


def test_load_config_migrates_legacy_tray_default_program(tmp_data_dir: Path):
    path = tmp_data_dir / "config.json"
    path.write_text(
        (
            "{\n"
            '  "tray": {\n'
            '    "daemon_program": ["uv", "run", "drclaw", "daemon", '
            '"--debug-full", "-f", "web"]\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.tray.daemon_program == TrayConfig().daemon_program


def test_config_corrupt_json(tmp_data_dir: Path):
    path = tmp_data_dir / "bad.json"
    path.write_text("{invalid json", encoding="utf-8")
    cfg = load_config(path)
    assert cfg == DrClawConfig()


def test_config_invalid_schema_returns_defaults(tmp_data_dir: Path):
    path = tmp_data_dir / "bad_schema.json"
    path.write_text('{"agent": {"max_iterations": "not_a_number"}}', encoding="utf-8")
    cfg = load_config(path)
    assert cfg == DrClawConfig()


def test_load_config_strict_raises_for_corrupt_json(tmp_data_dir: Path):
    path = tmp_data_dir / "bad.json"
    path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ConfigLoadError, match="Invalid config JSON"):
        load_config_strict(path)


def test_load_config_strict_raises_for_invalid_schema(tmp_data_dir: Path):
    path = tmp_data_dir / "bad_schema.json"
    path.write_text('{"agent": {"max_iterations": "not_a_number"}}', encoding="utf-8")

    with pytest.raises(ConfigLoadError, match="agent.max_iterations"):
        load_config_strict(path)


def test_load_config_strict_raises_for_missing_active_provider(tmp_data_dir: Path):
    path = tmp_data_dir / "bad_provider.json"
    path.write_text(
        '{"providers": {"default": {"model": "openai/gpt-4o"}},"active_provider": "missing"}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigLoadError, match="active_provider"):
        load_config_strict(path)


def test_daemon_config_default():
    cfg = DrClawConfig()
    assert cfg.daemon.frontends == []
    assert cfg.daemon.verbose_chat is True
    assert cfg.daemon.web_in_docker is False


def test_daemon_config_roundtrip(tmp_data_dir: Path):
    path = tmp_data_dir / "config.json"
    cfg = DrClawConfig(
        daemon=DaemonConfig(frontends=["telegram"], verbose_chat=False, web_in_docker=True)
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.daemon.frontends == ["telegram"]
    assert loaded.daemon.verbose_chat is False
    assert loaded.daemon.web_in_docker is True


def test_feishu_config_roundtrip(tmp_data_dir: Path):
    path = tmp_data_dir / "config.json"
    cfg = DrClawConfig(
        feishu=FeishuConfig(
            app_id="cli_xxx",
            app_secret="secret_xxx",
            encrypt_key="enc",
            verification_token="token",
            allow_from=["ou_1", "ou_2"],
            reconnect_interval_seconds=9,
        )
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.feishu.app_id == "cli_xxx"
    assert loaded.feishu.app_secret == "secret_xxx"
    assert loaded.feishu.encrypt_key == "enc"
    assert loaded.feishu.verification_token == "token"
    assert loaded.feishu.allow_from == ["ou_1", "ou_2"]
    assert loaded.feishu.reconnect_interval_seconds == 9


def test_serper_config_roundtrip(tmp_data_dir: Path):
    path = tmp_data_dir / "config.json"
    cfg = DrClawConfig(
        tools=ToolsConfig(
            web=WebToolsConfig(
                serper=SerperConfig(
                    api_key="serper-test-key",
                    endpoint="https://google.serper.dev/search",
                    max_results=7,
                )
            )
        )
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.tools.web.serper.api_key == "serper-test-key"
    assert loaded.tools.web.serper.endpoint == "https://google.serper.dev/search"
    assert loaded.tools.web.serper.max_results == 7


def test_env_config_roundtrip(tmp_data_dir: Path):
    path = tmp_data_dir / "config.json"
    cfg = DrClawConfig(
        env=EnvConfig(
            global_={"SERPER_API_KEY": "global-key"},
            scoped={"proj:abc": {"SERPER_API_KEY": "project-key"}},
        )
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.env.global_["SERPER_API_KEY"] == "global-key"
    assert loaded.env.scoped["proj:abc"]["SERPER_API_KEY"] == "project-key"


def test_tray_config_roundtrip(tmp_data_dir: Path):
    path = tmp_data_dir / "config.json"
    cfg = DrClawConfig(
        tray=TrayConfig(
            control_panel_url="http://127.0.0.1:9999",
            icon_path="~/custom.png",
            daemon_program=["drclaw", "daemon", "-f", "web"],
            daemon_env={"NO_PROXY": "example.com"},
            daemon_cwd="~/workspace/RBot",
            shutdown_timeout_seconds=15,
        )
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.tray.control_panel_url == "http://127.0.0.1:9999"
    assert loaded.tray.icon_path == "~/custom.png"
    assert loaded.tray.daemon_program == ["drclaw", "daemon", "-f", "web"]
    assert loaded.tray.daemon_env == {"NO_PROXY": "example.com"}
    assert loaded.tray.daemon_cwd == "~/workspace/RBot"
    assert loaded.tray.shutdown_timeout_seconds == 15


def test_external_agents_roundtrip(tmp_data_dir: Path):
    path = tmp_data_dir / "config.json"
    cfg = DrClawConfig(
        external_agents=[
            ExternalAgentConfig(
                id="chem",
                label="Chem External",
                request_url="http://127.0.0.1:9000/request",
                description="external chemistry helper",
                avatar="/assets/avatars/1.png",
                request_timeout_seconds=12,
                callback_timeout_seconds=180,
            )
        ]
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert len(loaded.external_agents) == 1
    ext = loaded.external_agents[0]
    assert ext.id == "chem"
    assert ext.label == "Chem External"
    assert ext.request_url == "http://127.0.0.1:9000/request"
    assert ext.description == "external chemistry helper"
    assert ext.avatar == "/assets/avatars/1.png"
    assert ext.request_timeout_seconds == 12
    assert ext.callback_timeout_seconds == 180
