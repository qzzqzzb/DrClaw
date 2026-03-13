"""FrontendAdapter — protocol that all UI/channel adapters implement."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from drclaw.daemon.kernel import Kernel


@runtime_checkable
class FrontendAdapter(Protocol):
    adapter_id: str

    async def start(self, kernel: Kernel) -> None: ...
    async def stop(self) -> None: ...
