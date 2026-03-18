"""Tests for OAuth provider CLI commands and _make_provider routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from drclaw.cli.app import _make_provider, app
from drclaw.config.schema import DrClawConfig, ProviderConfig

runner = CliRunner()


# ---------------------------------------------------------------------------
# provider login
# ---------------------------------------------------------------------------


def test_provider_login_unknown():
    result = runner.invoke(app, ["provider", "login", "no-such-thing"])
    assert result.exit_code != 0
    assert "Unknown OAuth provider" in result.output


def test_provider_login_lists_supported():
    result = runner.invoke(app, ["provider", "login", "no-such"])
    assert "openai-codex" in result.output
    assert "github-copilot" in result.output


# ---------------------------------------------------------------------------
# _make_provider routing
# ---------------------------------------------------------------------------


def test_make_provider_routes_codex():
    from drclaw.providers.openai_codex_provider import OpenAICodexProvider

    config = DrClawConfig(
        providers={"default": ProviderConfig(model="openai-codex/gpt-5.1-codex")}
    )
    provider = _make_provider(config)
    assert isinstance(provider, OpenAICodexProvider)


def test_make_provider_routes_codex_underscore():
    from drclaw.providers.openai_codex_provider import OpenAICodexProvider

    config = DrClawConfig(
        providers={"default": ProviderConfig(model="openai_codex/gpt-5.1-codex")}
    )
    provider = _make_provider(config)
    assert isinstance(provider, OpenAICodexProvider)


def test_make_provider_routes_copilot_to_litellm():
    from drclaw.providers.litellm_provider import LiteLLMProvider

    config = DrClawConfig(
        providers={"default": ProviderConfig(model="github_copilot/gpt-4o")}
    )
    provider = _make_provider(config)
    assert isinstance(provider, LiteLLMProvider)


def test_make_provider_routes_anthropic_to_litellm():
    from drclaw.providers.litellm_provider import LiteLLMProvider

    config = DrClawConfig(
        providers={"default": ProviderConfig(model="anthropic/claude-sonnet-4-5")}
    )
    provider = _make_provider(config)
    assert isinstance(provider, LiteLLMProvider)


def test_make_provider_routes_moonshot_to_litellm():
    from drclaw.providers.litellm_provider import LiteLLMProvider

    config = DrClawConfig(
        providers={"default": ProviderConfig(model="moonshot/kimi-k2.5")}
    )
    provider = _make_provider(config)
    assert isinstance(provider, LiteLLMProvider)
