"""Configuration schema using Pydantic."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderConfig(BaseModel):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    model: str = "anthropic/claude-sonnet-4-5"


class AgentConfig(BaseModel):
    """Agent behavior configuration."""

    max_iterations: int = 40
    max_tokens: int = 8192
    temperature: float = 0.1
    memory_window: int = 100
    max_history: int | None = Field(default=None, ge=1)
    tool_detach_timeout_seconds: float = Field(default=60, ge=0.01)


class DaemonConfig(BaseModel):
    """Daemon mode configuration."""

    frontends: list[str] = Field(default_factory=list)
    verbose_chat: bool = True
    show_tool_calls: bool = True


class TrayConfig(BaseModel):
    """Tray app configuration for macOS menu-bar integration."""

    control_panel_url: str = "http://127.0.0.1:8080"
    icon_path: str = ""
    daemon_program: list[str] = Field(
        default_factory=lambda: [
            "uv",
            "run",
            "drclaw",
            "daemon",
            "-f",
            "feishu",
            "--debug-full",
            "-f",
            "web",
        ]
    )
    daemon_env: dict[str, str] = Field(
        default_factory=lambda: {
            "NO_PROXY": "open.feishu.cn,open.feishu.cn,*.feishu.cn,*.larksuite.com",
        }
    )
    daemon_cwd: str = ""
    shutdown_timeout_seconds: int = Field(default=8, ge=1)


class FeishuConfig(BaseModel):
    """Feishu/Lark frontend configuration (WebSocket long connection)."""

    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    reconnect_interval_seconds: int = Field(default=5, ge=1)


class SerperConfig(BaseModel):
    """Serper web-search configuration."""

    api_key: str = ""
    endpoint: str = "https://google.serper.dev/search"
    max_results: int = Field(default=5, ge=1, le=10)


class WebToolsConfig(BaseModel):
    """Web tools configuration."""

    serper: SerperConfig = SerperConfig()


class ToolsConfig(BaseModel):
    """Tools configuration."""

    web: WebToolsConfig = WebToolsConfig()


class EnvConfig(BaseModel):
    """Scoped environment-variable configuration."""

    model_config = ConfigDict(populate_by_name=True)

    global_: dict[str, str] = Field(default_factory=dict, alias="global")
    scoped: dict[str, dict[str, str]] = Field(default_factory=dict)


class ClaudeCodeConfig(BaseModel):
    """Claude Code SDK integration configuration."""

    enabled: bool = False
    max_budget_usd: float = Field(default=5.0, ge=0.01)
    max_turns: int | None = Field(default=None, ge=1)
    permission_mode: str = "acceptEdits"
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["Read", "Write", "Edit", "Bash"]
    )
    max_concurrent_sessions: int = Field(default=4, ge=1, le=16)
    idle_timeout_seconds: int = Field(default=300, ge=30)
    env: dict[str, str] = Field(default_factory=dict)
    # Retry config: transient SDK/IPC errors are retried with exponential back-off.
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_base_delay_seconds: float = Field(default=1.0, ge=0.1)
    retry_max_delay_seconds: float = Field(default=30.0, ge=1.0)
    # MCP bridge: DrClaw tools to expose inside Claude Code sessions.
    # Empty list (default) keeps current behaviour — no extra tools exposed.
    # Use ["*"] to expose all non-claude_code tools, or list specific tool names.
    expose_tools_to_sessions: list[str] = Field(default_factory=list)


class ExternalAgentConfig(BaseModel):
    """External agent bridge configuration."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    request_url: str = Field(min_length=1)
    description: str = ""
    avatar: str = ""
    request_timeout_seconds: int = Field(default=10, ge=1, le=300)
    callback_timeout_seconds: int = Field(default=120, ge=1, le=3600)


class DrClawConfig(BaseModel):
    """Root configuration for DrClaw."""

    providers: dict[str, ProviderConfig] = Field(
        default_factory=lambda: {"default": ProviderConfig()}
    )
    active_provider: str = "default"
    agent: AgentConfig = AgentConfig()
    data_dir: str = "~/.drclaw"
    daemon: DaemonConfig = DaemonConfig()
    tray: TrayConfig = TrayConfig()
    feishu: FeishuConfig = FeishuConfig()
    tools: ToolsConfig = ToolsConfig()
    env: EnvConfig = EnvConfig()
    claude_code: ClaudeCodeConfig = ClaudeCodeConfig()
    external_agents: list[ExternalAgentConfig] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _migrate_single_provider_input(cls, data):  # noqa: ANN001
        """Accept legacy single-provider payloads in Python and JSON inputs."""
        if not isinstance(data, dict):
            return data
        if "provider" in data and "providers" not in data:
            migrated = dict(data)
            migrated["providers"] = {"default": migrated.pop("provider")}
            migrated.setdefault("active_provider", "default")
            return migrated
        return data

    @property
    def active_provider_config(self) -> ProviderConfig:
        cfg = self.providers.get(self.active_provider)
        if cfg is None:
            raise ValueError(f"active_provider {self.active_provider!r} not found in providers")
        return cfg

    @property
    def provider(self) -> ProviderConfig:
        """Legacy alias for the active provider config."""
        return self.active_provider_config

    @property
    def data_path(self) -> Path:
        """Expanded data directory as a Path."""
        return Path(self.data_dir).expanduser()
