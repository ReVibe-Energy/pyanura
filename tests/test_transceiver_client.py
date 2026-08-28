"""Tests for TransceiverClient connection handling, using a fake transport."""

import asyncio

import cbor2
import pytest

from anura.transceiver import models
from anura.transceiver.client import TransceiverClient
from anura.transceiver.exceptions import (
    TransceiverConnectionError,
    TransceiverError,
)
from anura.transceiver.transport import TCPTransport, USBTransport
from anura.transceiver.transport.base import Transport


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


class FakeTransport(Transport, transport_type="fake-test"):
    """In-memory transport speaking just enough CBOR-RPC for the client.

    Answers method discovery immediately. Pings are counted and answered
    only when ``answer_pings`` is set. The "slow_op" method is answered
    after ``slow_op_delay`` seconds, and "never_op" is never answered.
    """

    def __init__(self, *, answer_pings: bool = True, slow_op_delay: float = 0.0):
        self._read_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self.answer_pings = answer_pings
        self.slow_op_delay = slow_op_delay
        self.ping_count = 0
        self.never_op_count = 0

    async def open_connection(self) -> None:
        pass

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()

    async def send(self, payload: bytes, /) -> None:
        match cbor2.loads(payload):
            case [models.msg_type.Request, token, ".well-known/methods", _]:
                self._respond(token, None, {})
            case [models.msg_type.Request, token, "ping", arg]:
                self.ping_count += 1
                if self.answer_pings:
                    self._respond(token, None, arg)
            case [models.msg_type.Request, token, "slow_op", _]:
                self._tasks.append(asyncio.create_task(self._respond_later(token)))
            case [models.msg_type.Request, _, "never_op", _]:
                self.never_op_count += 1
            case message:
                raise AssertionError(f"Unexpected message: {message}")

    async def read(self) -> bytes:
        return await self._read_queue.get()

    def _respond(self, token, error, result) -> None:
        self._read_queue.put_nowait(
            cbor2.dumps([models.msg_type.Response, token, error, result])
        )

    async def _respond_later(self, token) -> None:
        await asyncio.sleep(self.slow_op_delay)
        self._respond(token, None, "done")


def make_client(transport: FakeTransport) -> TransceiverClient:
    client = TransceiverClient("host-is-unused")
    client._transport = transport
    client._keepalive_interval = 0.05
    client._request_timeout = 0.1
    return client


def test_keepalive_pings_flow_while_idle():
    async def scenario():
        transport = FakeTransport()
        client = make_client(transport)
        await client.connect()
        try:
            await asyncio.sleep(0.3)
            assert transport.ping_count >= 2
            assert not client._connection_closed.is_set()
        finally:
            await client.disconnect()

    run(scenario())


def test_keepalive_pings_flow_while_a_request_is_in_flight():
    """The server evicts clients it hears nothing from, so the pings must
    keep flowing even while a request is waiting for its response."""

    async def scenario():
        transport = FakeTransport(slow_op_delay=0.3)
        client = make_client(transport)
        await client.connect()
        try:
            pings_before = transport.ping_count
            assert await client.request("slow_op", timeout=1.0) == "done"
            assert transport.ping_count - pings_before >= 2
        finally:
            await client.disconnect()

    run(scenario())


def test_unanswered_keepalive_ping_kills_an_idle_connection():
    async def scenario():
        transport = FakeTransport(answer_pings=False)
        client = make_client(transport)
        await client.connect()
        try:
            await client.wait_for_disconnection()
            assert isinstance(client._connection_exception, TransceiverError)
            assert '"ping" request unanswered' in str(client._connection_exception)
        finally:
            await client.disconnect()

    run(scenario())


def test_dead_connection_fails_an_unbounded_request():
    """A request sent without a timeout must not hang forever on a dead
    connection: the keepalive ping times out and closes it."""

    async def scenario():
        transport = FakeTransport(answer_pings=False)
        client = make_client(transport)
        await client.connect()
        try:
            with pytest.raises(TransceiverConnectionError, match="Connection broken"):
                await client.request("never_op", timeout=None)
        finally:
            await client.disconnect()

    run(scenario())


def test_no_keepalive_when_transport_opts_out():
    async def scenario():
        transport = FakeTransport(answer_pings=False)
        transport.requires_keepalive = False
        client = make_client(transport)
        await client.connect()
        try:
            # No pings are sent, so nothing times out on an idle connection.
            await asyncio.sleep(0.3)
            assert transport.ping_count == 0
            assert not client._connection_closed.is_set()
            # The connection still services requests.
            assert await client.request("slow_op") == "done"
        finally:
            await client.disconnect()

    run(scenario())


def test_transport_keepalive_flags():
    assert TCPTransport.requires_keepalive is True
    assert USBTransport.requires_keepalive is False


def test_unanswered_request_fails_the_connection():
    """The transceiver answers every request promptly, so an unanswered one
    means it is broken and the connection is closed for the owner to
    reconnect."""

    async def scenario():
        transport = FakeTransport()
        client = make_client(transport)
        await client.connect()
        try:
            with pytest.raises(TransceiverConnectionError, match="connection closed"):
                await client.request("never_op")
            assert transport.never_op_count == 1
            assert client._connection_closed.is_set()
            assert '"never_op" request unanswered' in str(client._connection_exception)
        finally:
            await client.disconnect()

    run(scenario())


def test_request_timeout_can_be_raised_and_disabled_per_call():
    async def scenario():
        transport = FakeTransport(slow_op_delay=0.2)
        client = make_client(transport)
        await client.connect()
        try:
            client._request_timeout = 0.05
            assert await client.request("slow_op", timeout=1.0) == "done"
            assert await client.request("slow_op", timeout=None) == "done"
        finally:
            await client.disconnect()

    run(scenario())
