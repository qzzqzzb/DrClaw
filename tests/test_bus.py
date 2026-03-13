"""Tests for the message bus and message types."""

import asyncio

import pytest

from drclaw.bus.queue import MessageBus
from drclaw.models.messages import InboundMessage, OutboundMessage


def test_inbound_session_key_default():
    msg = InboundMessage(channel="cli", chat_id="main", text="hello")
    assert msg.session_key == "cli:main"


def test_inbound_session_key_override():
    msg = InboundMessage(
        channel="slack", chat_id="U123", text="hi", session_key_override="slack:thread-456"
    )
    assert msg.session_key == "slack:thread-456"


def test_outbound_session_key():
    msg = OutboundMessage(channel="telegram", chat_id="987654", text="reply")
    assert msg.session_key == "telegram:987654"


@pytest.mark.asyncio
async def test_bus_roundtrip():
    bus = MessageBus()

    inbound = InboundMessage(channel="cli", chat_id="main", text="ping")
    await bus.publish_inbound(inbound)
    received = await bus.consume_inbound()
    assert received is inbound

    outbound = OutboundMessage(channel="cli", chat_id="main", text="pong")
    await bus.publish_outbound(outbound)
    sent = await bus.consume_outbound()
    assert sent is outbound


# ---------------------------------------------------------------------------
# New message model tests
# ---------------------------------------------------------------------------


def test_inbound_hop_count_default():
    msg = InboundMessage("cli", "m", "hi")
    assert msg.hop_count == 0


def test_outbound_source_default():
    msg = OutboundMessage("cli", "m", "hi")
    assert msg.source == "main"


def test_existing_message_construction_unchanged():
    """Positional construction still works with new fields having defaults."""
    inbound = InboundMessage("cli", "main", "hello")
    assert inbound.channel == "cli"
    assert inbound.chat_id == "main"
    assert inbound.text == "hello"
    assert inbound.session_key_override is None
    assert inbound.hop_count == 0

    outbound = OutboundMessage("cli", "main", "reply")
    assert outbound.channel == "cli"
    assert outbound.chat_id == "main"
    assert outbound.text == "reply"
    assert outbound.source == "main"


# ---------------------------------------------------------------------------
# Topic-based bus tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_topic_main():
    """No topic arg works — backward compat via default 'main' topic."""
    bus = MessageBus()
    msg = InboundMessage("cli", "main", "hello")
    await bus.publish_inbound(msg)
    received = await bus.consume_inbound()
    assert received is msg


@pytest.mark.asyncio
async def test_topic_routing_isolation():
    """Messages published to topic A are not visible to topic B consumer."""
    bus = MessageBus()
    bus.subscribe("a")
    bus.subscribe("b")

    msg_a = InboundMessage("cli", "a", "for a")
    await bus.publish_inbound(msg_a, topic="a")

    # Topic B queue should be empty
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(bus.consume_inbound("b"), timeout=0.05)

    # Topic A should have the message
    received = await asyncio.wait_for(bus.consume_inbound("a"), timeout=0.1)
    assert received is msg_a


@pytest.mark.asyncio
async def test_publish_auto_subscribes():
    """Publishing to an unknown topic auto-creates the queue (no message loss)."""
    bus = MessageBus()
    msg = InboundMessage("cli", "main", "early")
    await bus.publish_inbound(msg, topic="new")

    # Consumer can retrieve the message even though no explicit subscribe happened
    received = await asyncio.wait_for(bus.consume_inbound("new"), timeout=0.1)
    assert received is msg


@pytest.mark.asyncio
async def test_outbound_shared():
    """Multiple topics share a single outbound queue."""
    bus = MessageBus()
    bus.subscribe("a")
    bus.subscribe("b")

    out1 = OutboundMessage("cli", "a", "from a")
    out2 = OutboundMessage("cli", "b", "from b")
    await bus.publish_outbound(out1)
    await bus.publish_outbound(out2)

    received1 = await bus.consume_outbound()
    received2 = await bus.consume_outbound()
    assert received1 is out1
    assert received2 is out2


@pytest.mark.asyncio
async def test_consume_auto_subscribes():
    """consume_inbound on unknown topic creates queue automatically."""
    bus = MessageBus()
    # Don't call subscribe — consume should auto-create
    assert "new_topic" not in bus._topics

    # Publish after consume starts waiting (via task)
    async def publish_delayed():
        await asyncio.sleep(0.01)
        # Now topic exists (auto-subscribed by consume)
        await bus.publish_inbound(InboundMessage("cli", "x", "auto"), topic="new_topic")

    task = asyncio.create_task(publish_delayed())
    received = await asyncio.wait_for(bus.consume_inbound("new_topic"), timeout=1.0)
    await task
    assert received.text == "auto"


# ---------------------------------------------------------------------------
# InboundMessage.source field tests
# ---------------------------------------------------------------------------


def test_inbound_source_default():
    msg = InboundMessage("cli", "main", "hi")
    assert msg.source == "user"


def test_inbound_source_custom():
    msg = InboundMessage("cli", "main", "hi", source="telegram")
    assert msg.source == "telegram"


# ---------------------------------------------------------------------------
# Outbound fan-out tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_fanout_two_subscribers():
    bus = MessageBus()
    q1 = bus.subscribe_outbound("s1")
    q2 = bus.subscribe_outbound("s2")

    msg = OutboundMessage("cli", "main", "hello")
    await bus.publish_outbound(msg)

    r1 = await asyncio.wait_for(q1.get(), timeout=0.1)
    r2 = await asyncio.wait_for(q2.get(), timeout=0.1)
    assert r1 is msg
    assert r2 is msg


@pytest.mark.asyncio
async def test_outbound_unsubscribe():
    bus = MessageBus()
    q1 = bus.subscribe_outbound("s1")
    bus.subscribe_outbound("s2")
    bus.unsubscribe_outbound("s2")

    msg = OutboundMessage("cli", "main", "hello")
    await bus.publish_outbound(msg)

    r1 = await asyncio.wait_for(q1.get(), timeout=0.1)
    assert r1 is msg
    # s1 + __legacy__ (auto-created in __init__)
    assert "s2" not in bus._outbound_subs


@pytest.mark.asyncio
async def test_consume_outbound_backward_compat():
    """consume_outbound() still works via __legacy__ subscription."""
    bus = MessageBus()
    msg = OutboundMessage("cli", "main", "compat")
    await bus.publish_outbound(msg)
    received = await asyncio.wait_for(bus.consume_outbound(), timeout=0.1)
    assert received is msg


@pytest.mark.asyncio
async def test_subscribe_outbound_evicts_legacy():
    """Registering a real subscriber auto-removes __legacy__ to prevent memory leak."""
    bus = MessageBus()
    assert "__legacy__" in bus._outbound_subs
    bus.subscribe_outbound("repl")
    assert "__legacy__" not in bus._outbound_subs


@pytest.mark.asyncio
async def test_publish_outbound_no_subscribers():
    """Publishing with no subscribers is a no-op (no error)."""
    bus = MessageBus()
    bus._outbound_subs.clear()
    msg = OutboundMessage("cli", "main", "void")
    await bus.publish_outbound(msg)  # should not raise


# ---------------------------------------------------------------------------
# Inbound display fan-out tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_display_fanout():
    bus = MessageBus()
    dq = bus.subscribe_inbound_display("display1")

    msg = InboundMessage("cli", "main", "user says hi")
    await bus.publish_inbound(msg)

    received = await asyncio.wait_for(dq.get(), timeout=0.1)
    assert received is msg


@pytest.mark.asyncio
async def test_inbound_still_routes_to_agent():
    """Display fan-out doesn't break normal topic routing."""
    bus = MessageBus()
    bus.subscribe_inbound_display("display1")

    msg = InboundMessage("cli", "main", "routed")
    await bus.publish_inbound(msg, topic="main")

    received = await asyncio.wait_for(bus.consume_inbound("main"), timeout=0.1)
    assert received is msg


@pytest.mark.asyncio
async def test_inbound_display_source_preserved():
    bus = MessageBus()
    dq = bus.subscribe_inbound_display("d1")

    msg = InboundMessage("cli", "main", "hi", source="telegram")
    await bus.publish_inbound(msg)

    received = await asyncio.wait_for(dq.get(), timeout=0.1)
    assert received.source == "telegram"


@pytest.mark.asyncio
async def test_inbound_publish_hook_runs_before_queue():
    bus = MessageBus()
    seen: list[tuple[str, str]] = []

    async def hook(topic: str, message: InboundMessage) -> None:
        seen.append((topic, message.text))

    bus.set_on_inbound_publish(hook)
    msg = InboundMessage("cli", "main", "hooked")
    await bus.publish_inbound(msg, topic="main")

    received = await asyncio.wait_for(bus.consume_inbound("main"), timeout=0.1)
    assert received is msg
    assert seen == [("main", "hooked")]


@pytest.mark.asyncio
async def test_inbound_publish_hook_failure_does_not_block_queue():
    bus = MessageBus()

    async def hook(_topic: str, _message: InboundMessage) -> None:
        raise RuntimeError("boom")

    bus.set_on_inbound_publish(hook)
    msg = InboundMessage("cli", "main", "still routes")
    await bus.publish_inbound(msg, topic="main")

    received = await asyncio.wait_for(bus.consume_inbound("main"), timeout=0.1)
    assert received is msg
