"""Message bus — routes messages between channels and agent loops via topics."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable

from loguru import logger

from drclaw.models.messages import InboundMessage, OutboundMessage


class MessageBus:
    """Topic-based message bus: per-topic inbound queues, fan-out outbound.

    Subscribers MUST NOT mutate received messages — the same object is
    delivered to every subscriber queue.
    """

    def __init__(self) -> None:
        self._topics: dict[str, asyncio.Queue[InboundMessage]] = {}
        self._outbound_subs: dict[str, asyncio.Queue[OutboundMessage]] = {}
        self._inbound_display_subs: dict[str, asyncio.Queue[InboundMessage]] = {}
        self._on_inbound_publish: (
            Callable[[str, InboundMessage], Awaitable[None] | None] | None
        ) = None
        # Auto-subscribe the default topic for backward compatibility
        self.subscribe("main")
        # Legacy outbound subscription so consume_outbound() works immediately
        self.subscribe_outbound("__legacy__")

    def subscribe(self, topic: str) -> None:
        if topic not in self._topics:
            self._topics[topic] = asyncio.Queue()

    # -- Outbound fan-out --------------------------------------------------

    def subscribe_outbound(self, subscriber_id: str) -> asyncio.Queue[OutboundMessage]:
        """Register an outbound subscriber. Removes __legacy__ if a real subscriber registers."""
        if subscriber_id != "__legacy__":
            self._outbound_subs.pop("__legacy__", None)
        if subscriber_id not in self._outbound_subs:
            self._outbound_subs[subscriber_id] = asyncio.Queue()
        return self._outbound_subs[subscriber_id]

    def unsubscribe_outbound(self, subscriber_id: str) -> None:
        self._outbound_subs.pop(subscriber_id, None)

    async def publish_outbound(self, message: OutboundMessage) -> None:
        for q in list(self._outbound_subs.values()):
            await q.put(message)

    async def consume_outbound(self) -> OutboundMessage:
        """Legacy single-consumer API — uses a dedicated __legacy__ subscription."""
        q = self.subscribe_outbound("__legacy__")
        return await q.get()

    # -- Inbound routing + display fan-out ---------------------------------

    def subscribe_inbound_display(self, sub_id: str) -> asyncio.Queue[InboundMessage]:
        if sub_id not in self._inbound_display_subs:
            self._inbound_display_subs[sub_id] = asyncio.Queue()
        return self._inbound_display_subs[sub_id]

    def unsubscribe_inbound_display(self, sub_id: str) -> None:
        self._inbound_display_subs.pop(sub_id, None)

    def set_on_inbound_publish(
        self,
        callback: Callable[[str, InboundMessage], Awaitable[None] | None] | None,
    ) -> None:
        """Register an optional hook invoked before inbound messages are queued."""
        self._on_inbound_publish = callback

    async def publish_inbound(self, message: InboundMessage, topic: str = "main") -> None:
        message.topic = topic
        self.subscribe(topic)
        if self._on_inbound_publish is not None:
            try:
                maybe = self._on_inbound_publish(topic, message)
                if inspect.isawaitable(maybe):
                    await maybe
            except Exception as exc:
                logger.warning("Inbound publish hook failed for topic {}: {}", topic, exc)
        await self._topics[topic].put(message)
        for q in list(self._inbound_display_subs.values()):
            await q.put(message)

    async def consume_inbound(self, topic: str = "main") -> InboundMessage:
        self.subscribe(topic)
        return await self._topics[topic].get()
