"""Tests for TransceiverClient connection handling, using a fake transport."""

import asyncio

import cbor2

from anura.transceiver import models
from anura.transceiver.client import TransceiverClient
from anura.transceiver.exceptions import TransceiverError
from anura.transceiver.transport import TCPTransport, USBTransport
from anura.transceiver.transport.base import Transport


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


class FakeTransport(Transport, transport_type="fake-test"):
    """In-memory transport speaking just enough CBOR-RPC for the client.

    Answers method discovery immediately. Pings are counted and answered
    only when ``answer_pings`` is set. The "slow_op" method is answered
    after ``slow_op_delay`` seconds.
    """

    def __init__(self, *, answer_pings: bool = True, slow_op_delay: float = 0.0):
        self._read_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self.answer_pings = answer_pings
        self.slow_op_delay = slow_op_delay
        self.ping_count = 0

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
    client._keepalive_timeout = 0.05
    return client


def test_keepalive_keeps_answering_idle_connection_alive():
    async def scenario():
        transport = FakeTransport(answer_pings=True)
        client = make_client(transport)
        await client.connect()
        try:
            await asyncio.sleep(0.3)
            assert transport.ping_count >= 2
            assert not client._connection_closed.is_set()
        finally:
            await client.disconnect()

    run(scenario())


def test_keepalive_detects_dead_idle_connection():
    async def scenario():
        transport = FakeTransport(answer_pings=False)
        client = make_client(transport)
        await client.connect()
        try:
            await client.wait_for_disconnection()
            assert isinstance(client._connection_exception, TransceiverError)
            assert "Keepalive ping timed out" in str(client._connection_exception)
        finally:
            await client.disconnect()

    run(scenario())


def test_no_keepalive_probing_when_transport_opts_out():
    async def scenario():
        transport = FakeTransport(answer_pings=False)
        transport.requires_keepalive = False
        client = make_client(transport)
        await client.connect()
        try:
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
