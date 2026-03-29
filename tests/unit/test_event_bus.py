"""Tests for the async event bus."""

import pytest
from alphaloop.core.events import EventBus, SignalGenerated, TradeOpened


@pytest.mark.asyncio
async def test_publish_subscribe():
    bus = EventBus()
    received = []

    async def handler(event: SignalGenerated):
        received.append(event.symbol)

    bus.subscribe(SignalGenerated, handler)
    await bus.publish(SignalGenerated(symbol="XAUUSD"))

    assert received == ["XAUUSD"]


@pytest.mark.asyncio
async def test_no_cross_event_delivery():
    bus = EventBus()
    received = []

    async def handler(event: SignalGenerated):
        received.append("signal")

    bus.subscribe(SignalGenerated, handler)
    await bus.publish(TradeOpened(symbol="XAUUSD"))

    assert received == []


@pytest.mark.asyncio
async def test_failing_handler_does_not_block_others():
    bus = EventBus()
    received = []

    async def bad_handler(event: SignalGenerated):
        raise ValueError("boom")

    async def good_handler(event: SignalGenerated):
        received.append("ok")

    bus.subscribe(SignalGenerated, bad_handler)
    bus.subscribe(SignalGenerated, good_handler)
    await bus.publish(SignalGenerated(symbol="XAUUSD"))

    assert received == ["ok"]


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = EventBus()
    received = []

    async def handler(event: SignalGenerated):
        received.append(event.symbol)

    bus.subscribe(SignalGenerated, handler)
    bus.unsubscribe(SignalGenerated, handler)
    await bus.publish(SignalGenerated(symbol="XAUUSD"))

    assert received == []
