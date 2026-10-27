"""Tests for the control point response slot of BleakAVSSTransport."""

import asyncio
from collections.abc import Callable

import pytest

# The transport imports bleak at module scope. It comes with the "ble" extra,
# which `uv run pytest` does not install on its own; CI uses --all-extras.
pytest.importorskip("bleak", reason="BleakAVSSTransport needs the 'ble' extra")

from bleak.exc import BleakError

from anura.avss import uuids
from anura.avss.exceptions import AVSSConnectionError, AVSSError
from anura.avss.protocol import OpCode, ResponseCode
from anura.avss.transport import bleak as bleak_transport
from anura.avss.transport.bleak import BleakAVSSTransport

ADDR = "C0:00:00:00:00:01"

# One request, sent twice: an attempt that failed, and the retry of it.
REQUEST = bytes([OpCode.RESET_SETTINGS])

# The answer to the first attempt. A retry of the same opcode is owed an answer
# that looks exactly like this one, so neither the transport nor AVSSClient's
# response-opcode check can tell the two apart: handing this one to the retry
# reports a settings reset as done without the retry ever being answered.
ANSWER_TO_FIRST = bytes([OpCode.RESPONSE, OpCode.RESET_SETTINGS, ResponseCode.OK])


class FakeBleakClient:
    """Stands in for BleakClient.

    ``write_error`` and ``disconnect_error`` are raised by the corresponding
    call while they are set. ``indicate`` delivers a control point indication
    the way the real client dispatches a notification.
    """

    def __init__(self, addr, disconnected_callback=None):
        self.addr = addr
        self.disconnected_callback = disconnected_callback
        self.handlers: dict[str, Callable[[object, bytes], None]] = {}
        self.write_error: Exception | None = None
        self.disconnect_error: Exception | None = None

    async def connect(self):
        pass

    async def start_notify(self, uuid, handler):
        self.handlers[uuid] = handler

    async def write_gatt_char(self, uuid, value, response=True):
        if self.write_error is not None:
            raise self.write_error

    async def disconnect(self):
        if self.disconnect_error is not None:
            raise self.disconnect_error
        if self.disconnected_callback is not None:
            self.disconnected_callback(self)

    def indicate(self, data: bytes) -> None:
        self.handlers[uuids.ControlPointCharacteristicUuid](None, data)


def test_a_late_response_is_not_given_to_the_retry(monkeypatch):
    monkeypatch.setattr(bleak_transport, "BleakClient", FakeBleakClient)

    async def scenario():
        transport = BleakAVSSTransport(ADDR)
        await transport.open()
        client = transport._client
        assert isinstance(client, FakeBleakClient)

        # The write fails, which per the transport's own comment may still
        # mean the request reached the node. Closing is how it gets out of
        # that, so take the disconnect away from it too.
        client.write_error = BleakError("write failed")
        client.disconnect_error = BleakError("disconnect failed")

        with pytest.raises(AVSSConnectionError, match="Control Point write failed"):
            await transport.control_point_request(REQUEST, timeout=5.0)

        # The caller is told the request did not get through, so it retries.
        client.write_error = None
        retry = asyncio.create_task(
            transport.control_point_request(REQUEST, timeout=5.0)
        )
        await asyncio.sleep(0)

        # Only now does the node answer the first attempt, which did reach it.
        client.indicate(ANSWER_TO_FIRST)

        # Anything but that answer is fine here: refusing the retry keeps the
        # ordering intact, and so does going on waiting for an answer of the
        # retry's own.
        try:
            answer = await asyncio.wait_for(retry, 0.1)
        except (AVSSError, TimeoutError):
            return

        assert answer != ANSWER_TO_FIRST, (
            "the retry was handed the answer to the abandoned request"
        )

    asyncio.run(scenario())
