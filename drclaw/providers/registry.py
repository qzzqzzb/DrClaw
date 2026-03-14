"""Provider registry — metadata for LLM providers.

Ported from nanobot. Only includes entries DrClaw needs today (OAuth providers
and the standard ones LiteLLM handles). Extend as needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata."""

    name: str
    keywords: tuple[str, ...]
    env_key: str
    display_name: str = ""
    litellm_prefix: str = ""
    skip_prefixes: tuple[str, ...] = ()
    default_api_base: str = ""
    is_gateway: bool = False
    is_local: bool = False
    is_oauth: bool = False
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="anthropic",
        keywords=("claude", "anthropic"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        litellm_prefix="anthropic",
        skip_prefixes=("anthropic/",),
    ),
    ProviderSpec(
        name="openai",
        keywords=("gpt", "o1", "o3", "o4"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
    ),
    ProviderSpec(
        name="openai_codex",
        keywords=("openai-codex",),
        env_key="",
        display_name="OpenAI Codex",
        default_api_base="https://chatgpt.com/backend-api",
        is_oauth=True,
    ),
    ProviderSpec(
        name="github_copilot",
        keywords=("github_copilot", "copilot"),
        env_key="",
        display_name="GitHub Copilot",
        litellm_prefix="github_copilot",
        skip_prefixes=("github_copilot/",),
        is_oauth=True,
    ),
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        litellm_prefix="deepseek",
        skip_prefixes=("deepseek/",),
    ),
    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Google Gemini",
        litellm_prefix="gemini",
        skip_prefixes=("gemini/",),
    ),
)


def find_by_model(model: str) -> ProviderSpec | None:
    """Match a provider by model-name keyword (case-insensitive). Skips gateways/local."""
    model_lower = model.lower()
    model_normalized = model_lower.replace("-", "_")
    model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
    normalized_prefix = model_prefix.replace("-", "_")
    std_specs = [s for s in PROVIDERS if not s.is_gateway and not s.is_local]

    # Prefer explicit provider prefix to avoid cross-matches.
    for spec in std_specs:
        if model_prefix and normalized_prefix == spec.name:
            return spec

    for spec in std_specs:
        if any(
            kw in model_lower or kw.replace("-", "_") in model_normalized
            for kw in spec.keywords
        ):
            return spec
    return None


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by name."""
    for spec in PROVIDERS:
        if spec.name == name:
            return spec
    return None
