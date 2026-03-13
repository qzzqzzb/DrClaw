"""Temporary test skill — meow like a cat."""

import random
from typing import Any

from drclaw.tools.base import Tool

_MEOWS = [
    "Meow~",
    "Mrrrow!",
    "Prrrrr... meow.",
    "MEOW! *knocks things off table*",
    "Mew? Mew mew.",
    "*stretches* ...meow.",
    "Nya~",
]


class MeowTool(Tool):
    """A cat-meow tool for testing skill invocation."""

    @property
    def name(self) -> str:
        return "meow"

    @property
    def description(self) -> str:
        return "Meow like a cat. Use when the user wants to hear a meow or anything cat-related."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "intensity": {
                    "type": "string",
                    "enum": ["soft", "normal", "loud"],
                    "description": "How intense the meow should be.",
                },
            },
            "required": [],
        }

    async def execute(self, params: dict[str, Any]) -> str:
        intensity = params.get("intensity", "normal")
        meow = random.choice(_MEOWS)  # noqa: S311
        if intensity == "soft":
            meow = meow.lower()
        elif intensity == "loud":
            meow = meow.upper()
        return f"[meow tool] {meow}"
