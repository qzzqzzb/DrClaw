"""Integration smoke tests for OAuth providers.

Skipped by default — require real OAuth tokens to run.
Run manually: pytest tests/test_oauth_integration.py -k "codex or copilot" --no-header -rN
"""

from __future__ import annotations

import pytest

from drclaw.config.schema import ProviderConfig
from drclaw.providers.base import LLMResponse


@pytest.mark.skip(reason="Requires real OpenAI Codex OAuth token")
async def test_codex_real_call():
    from drclaw.providers.openai_codex_provider import OpenAICodexProvider

    config = ProviderConfig(model="openai-codex/gpt-5.1-codex")
    provider = OpenAICodexProvider(config)
    resp = await provider.complete(
        messages=[{"role": "user", "content": "Say hello in one word."}],
        system="Be concise.",
    )
    assert isinstance(resp, LLMResponse)
    assert resp.stop_reason in ("end_turn", "max_tokens", "error")
    if resp.stop_reason != "error":
        assert resp.content


@pytest.mark.skip(reason="Requires GitHub Copilot auth")
async def test_copilot_real_call():
    from drclaw.providers.litellm_provider import LiteLLMProvider

    config = ProviderConfig(model="github_copilot/gpt-4o")
    provider = LiteLLMProvider(config)
    resp = await provider.complete(
        messages=[{"role": "user", "content": "Say hello in one word."}],
        system="Be concise.",
    )
    assert isinstance(resp, LLMResponse)
    assert resp.stop_reason in ("end_turn", "max_tokens", "error")
