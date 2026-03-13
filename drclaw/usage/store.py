"""Persistent agent usage accounting (tokens + USD costs)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class UsageTotals:
    """Aggregated usage totals for one scope."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    total_cost_usd: float = 0.0

    def add(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        input_cost_usd: float,
        output_cost_usd: float,
        total_cost_usd: float,
    ) -> None:
        self.input_tokens += max(0, int(input_tokens))
        self.output_tokens += max(0, int(output_tokens))
        self.total_tokens += max(0, int(input_tokens)) + max(0, int(output_tokens))
        self.input_cost_usd += max(0.0, float(input_cost_usd))
        self.output_cost_usd += max(0.0, float(output_cost_usd))
        self.total_cost_usd += max(0.0, float(total_cost_usd))

    def to_dict(self) -> dict[str, int | float]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "input_cost_usd": self.input_cost_usd,
            "output_cost_usd": self.output_cost_usd,
            "total_cost_usd": self.total_cost_usd,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> UsageTotals:
        def _int(key: str) -> int:
            value = raw.get(key, 0)
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0

        def _float(key: str) -> float:
            value = raw.get(key, 0.0)
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return 0.0

        return cls(
            input_tokens=_int("input_tokens"),
            output_tokens=_int("output_tokens"),
            total_tokens=_int("total_tokens"),
            input_cost_usd=_float("input_cost_usd"),
            output_cost_usd=_float("output_cost_usd"),
            total_cost_usd=_float("total_cost_usd"),
        )


class AgentUsageStore:
    """Tracks all-time persisted usage and current-daemon-run usage."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._all_time: dict[str, UsageTotals] = {}
        self._this_run: dict[str, UsageTotals] = {}
        self._load()

    def record(
        self,
        *,
        agent_id: str,
        input_tokens: int,
        output_tokens: int,
        input_cost_usd: float,
        output_cost_usd: float,
        total_cost_usd: float,
    ) -> None:
        normalized_agent_id = (agent_id or "").strip()
        if not normalized_agent_id:
            return

        if normalized_agent_id not in self._all_time:
            self._all_time[normalized_agent_id] = UsageTotals()
        if normalized_agent_id not in self._this_run:
            self._this_run[normalized_agent_id] = UsageTotals()

        self._all_time[normalized_agent_id].add(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_usd=input_cost_usd,
            output_cost_usd=output_cost_usd,
            total_cost_usd=total_cost_usd,
        )
        self._this_run[normalized_agent_id].add(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_usd=input_cost_usd,
            output_cost_usd=output_cost_usd,
            total_cost_usd=total_cost_usd,
        )
        self._save()

    def known_agent_ids(self) -> set[str]:
        return set(self._all_time.keys()) | set(self._this_run.keys())

    def snapshot(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        totals_all = UsageTotals()
        totals_run = UsageTotals()

        for agent_id in sorted(self.known_agent_ids()):
            all_time = self._all_time.get(agent_id, UsageTotals())
            this_run = self._this_run.get(agent_id, UsageTotals())
            rows.append(
                {
                    "agent_id": agent_id,
                    "all_time": all_time.to_dict(),
                    "this_run": this_run.to_dict(),
                }
            )
            totals_all.add(
                input_tokens=all_time.input_tokens,
                output_tokens=all_time.output_tokens,
                input_cost_usd=all_time.input_cost_usd,
                output_cost_usd=all_time.output_cost_usd,
                total_cost_usd=all_time.total_cost_usd,
            )
            totals_run.add(
                input_tokens=this_run.input_tokens,
                output_tokens=this_run.output_tokens,
                input_cost_usd=this_run.input_cost_usd,
                output_cost_usd=this_run.output_cost_usd,
                total_cost_usd=this_run.total_cost_usd,
            )

        return {
            "agents": rows,
            "totals": {
                "all_time": totals_all.to_dict(),
                "this_run": totals_run.to_dict(),
            },
        }

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load usage store from {}: {}", self.path, exc)
            return

        if not isinstance(payload, dict):
            return
        agents = payload.get("agents")
        if not isinstance(agents, dict):
            return

        loaded: dict[str, UsageTotals] = {}
        for agent_id, raw in agents.items():
            if not isinstance(agent_id, str) or not isinstance(raw, dict):
                continue
            normalized = agent_id.strip()
            if not normalized:
                continue
            loaded[normalized] = UsageTotals.from_dict(raw)
        self._all_time = loaded

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "agents": {
                aid: totals.to_dict()
                for aid, totals in sorted(self._all_time.items(), key=lambda item: item[0])
            },
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
