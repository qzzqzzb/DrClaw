"""LiteLLM-backed LLM provider."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import litellm
from loguru import logger

from drclaw.config.schema import AgentConfig, ProviderConfig
from drclaw.providers.base import LLMProvider, LLMResponse, ToolCallRequest

litellm.suppress_debug_info = True

# Map litellm finish_reason values to normalized stop_reason strings.
_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "error": "error",
}

_CLAUDE_DOT_SERIES_RE = re.compile(
    r"^claude-(?P<major>\d+)(?:[.-](?P<minor>\d+))?-(?P<tier>opus|sonnet|haiku)(?:-(?P<date>\d{8}))?$"
)
_TRAILING_DATE_RE = re.compile(r"^(?P<base>.+)-(?P<date>\d{8})$")

# Single source of truth for agent-level defaults.
_AGENT_DEFAULTS = AgentConfig()


class LiteLLMProvider(LLMProvider):
    """LLM provider backed by litellm — supports any litellm-compatible model string."""

    def __init__(
        self,
        config: ProviderConfig,
        max_tokens: int = _AGENT_DEFAULTS.max_tokens,
        temperature: float = _AGENT_DEFAULTS.temperature,
    ) -> None:
        self._model = config.model
        self._api_key = config.api_key or None
        self._api_base = config.api_base
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        """Call litellm.acompletion and return a normalized LLMResponse."""
        all_messages: list[dict[str, Any]] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": all_messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if tools:
            kwargs["tools"] = tools

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = await litellm.acompletion(**kwargs)
                return self._parse_response(response)
            except Exception as exc:
                err_msg = str(exc).lower()
                retryable = (
                    "server disconnected" in err_msg
                    or "connection reset" in err_msg
                    or "connection closed" in err_msg
                    or "broken pipe" in err_msg
                    or "timed out" in err_msg
                )
                if not retryable or attempt == 2:
                    raise
                last_exc = exc
                wait = 1.0 * (attempt + 1)
                logger.warning(
                    "LLM call failed (attempt {}/3), retrying in {:.0f}s: {}",
                    attempt + 1, wait, exc,
                )
                await asyncio.sleep(wait)
        raise last_exc  # unreachable, but keeps type checker happy

    def _parse_response(self, response: Any) -> LLMResponse:
        choice = response.choices[0]
        message = choice.message
        finish_reason = choice.finish_reason or "stop"

        stop_reason = _FINISH_REASON_MAP.get(finish_reason)
        if stop_reason is None:
            logger.warning("Unknown finish_reason {!r}; treating as 'unknown'", finish_reason)
            stop_reason = "unknown"

        content = message.content or None

        tool_calls: list[ToolCallRequest] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                raw_args = self._normalize_tool_arguments(tc.function.name, tc.function.arguments)
                tool_calls.append(
                    ToolCallRequest(id=tc.id, name=tc.function.name, arguments=raw_args)
                )

        usage = response.usage
        input_tokens = int(usage.prompt_tokens) if usage and usage.prompt_tokens else 0
        output_tokens = int(usage.completion_tokens) if usage and usage.completion_tokens else 0
        model = getattr(response, "model", None) or self._model
        input_cost_usd, output_cost_usd, total_cost_usd = self._extract_costs(
            response=response,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            input_cost_usd=input_cost_usd,
            output_cost_usd=output_cost_usd,
            total_cost_usd=total_cost_usd,
        )

    def _extract_costs(
        self,
        *,
        response: Any,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> tuple[float, float, float]:
        input_cost = 0.0
        output_cost = 0.0
        hidden_total = self._extract_hidden_total_cost(response)

        cost_lookup_succeeded = False
        for lookup_model in self._cost_lookup_model_candidates(model):
            try:
                raw_costs = litellm.cost_per_token(
                    model=lookup_model,
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    response=response,
                )
            except Exception:
                continue

            cost_lookup_succeeded = True
            if isinstance(raw_costs, tuple) and len(raw_costs) >= 2:
                parsed_input = self._parse_non_negative_float(raw_costs[0])
                parsed_output = self._parse_non_negative_float(raw_costs[1])
                if parsed_input is not None:
                    input_cost = parsed_input
                if parsed_output is not None:
                    output_cost = parsed_output
            break

        if not cost_lookup_succeeded:
            logger.debug("Failed to calculate litellm token costs for model '{}'", model)

        total_cost = input_cost + output_cost
        if hidden_total is not None:
            total_cost = hidden_total
        return input_cost, output_cost, total_cost

    @staticmethod
    def _cost_lookup_model_candidates(model: str) -> list[str]:
        candidates: list[str] = [model]
        provider, model_name = model.split("/", 1) if "/" in model else ("", model)

        def _add(name: str) -> None:
            if not name:
                return
            if name not in candidates:
                candidates.append(name)

        canonical = LiteLLMProvider._canonicalize_claude_model_name(model_name)
        if canonical and canonical != model_name:
            if provider:
                _add(f"{provider}/{canonical}")
            _add(canonical)

        for name in (model_name, canonical):
            if not name:
                continue
            match = _TRAILING_DATE_RE.match(name)
            if not match:
                continue
            base = match.group("base")
            if provider:
                _add(f"{provider}/{base}")
            _add(base)

        return candidates

    @staticmethod
    def _canonicalize_claude_model_name(model_name: str) -> str | None:
        """
        Normalize dotted Anthropic names for cost lookup.

        Example:
          claude-4.6-opus-20260205 -> claude-opus-4-6-20260205
        """
        match = _CLAUDE_DOT_SERIES_RE.match(model_name)
        if not match:
            return None

        parts = ["claude", match.group("tier"), match.group("major")]
        minor = match.group("minor")
        if minor is not None:
            parts.append(minor)
        date = match.group("date")
        if date is not None:
            parts.append(date)
        return "-".join(parts)

    @staticmethod
    def _extract_hidden_total_cost(response: Any) -> float | None:
        hidden = getattr(response, "_hidden_params", None)
        if not isinstance(hidden, dict):
            return None
        return LiteLLMProvider._parse_non_negative_float(hidden.get("response_cost"))

    @staticmethod
    def _parse_non_negative_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed < 0:
            return None
        return parsed

    def _normalize_tool_arguments(self, tool_name: str, raw_args: Any) -> dict[str, Any]:
        """Return tool arguments as an object, recovering from malformed model output."""
        parsed: Any = raw_args
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Malformed JSON in tool call arguments for '{}': {}. Raw args: {!r}",
                    tool_name,
                    exc,
                    parsed[:300],
                )
                return {
                    "__parse_error__": str(exc),
                    "__raw_arguments__": parsed,
                }

        if isinstance(parsed, dict):
            return parsed

        logger.warning(
            "Tool call '{}' returned non-object arguments of type {}. Coercing to wrapper object.",
            tool_name,
            type(parsed).__name__,
        )
        return {"__raw_arguments__": parsed}
