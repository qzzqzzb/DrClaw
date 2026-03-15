from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from drclaw.frontends.web.adapter import WebAdapter as Adapter

__all__ = ["Adapter"]


def __getattr__(name: str):
    if name != "Adapter":
        raise AttributeError(name)
    from drclaw.frontends.web.adapter import WebAdapter as Adapter

    return Adapter
