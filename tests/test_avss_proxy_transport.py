"""Tests for the error mapping of ProxyAVSSTransport."""

import asyncio

import pytest

from anura.avss.exceptions import AVSSConnectionError, AVSSTransportError
from anura.avss.transport.proxy import ProxyAVSSTransport, _State
from anura.transceiver import models
from anura.transceiver.exceptions import (
    TransceiverConnectionError,
    TransceiverRequestError,
)

NODE = models.BluetoothAddrLE.parse("C0:00:00:00:00:01")


class FakeTransceiver:
    """Stands in for TransceiverClient; program writes raise ``outcome``."""

    def __init__(self, outcome: BaseException | None = None):
        self.outcome = outcome
        self.writes: list[bytes] = []

    async def avss_program_write(self, addr, data):
        assert addr == NODE
        self.writes.append(data)
        if self.outcome is not None:
            raise self.outcome


def open_transport(transceiver) -> ProxyAVSSTransport:
    transport = ProxyAVSSTransport(transceiver, NODE)  # type: ignore[arg-type]
    transport._state = _State.OPENED  # without the notification loop
    return transport


def request_error(code: models.APIErrorCode) -> TransceiverRequestError:
    return TransceiverRequestError("avss_program_write", models.APIError(code=code))


def test_program_write_passes_data_through():
    transceiver = FakeTransceiver()
    transport = open_transport(transceiver)

    asyncio.run(transport.program_write(b"chunk"))

    assert transceiver.writes == [b"chunk"]


def test_program_write_timeout_is_left_for_the_caller_to_retry():
    transport = open_transport(FakeTransceiver(TimeoutError("held too long")))

    with pytest.raises(TimeoutError):
        asyncio.run(transport.program_write(b"chunk"))


@pytest.mark.parametrize(
    "outcome",
    [
        request_error(models.APIErrorCode.NODE_UNAVAILABLE),
        TransceiverConnectionError("transceiver went away"),
    ],
    ids=["node-unavailable", "transceiver-connection-broken"],
)
def test_program_write_lost_connection_is_a_connection_error(outcome):
    transport = open_transport(FakeTransceiver(outcome))

    with pytest.raises(AVSSConnectionError):
        asyncio.run(transport.program_write(b"chunk"))


def test_program_write_other_request_errors_are_transport_errors():
    transport = open_transport(
        FakeTransceiver(request_error(models.APIErrorCode.OPERATION_FAILED))
    )

    with pytest.raises(AVSSTransportError, match="Program write failed"):
        asyncio.run(transport.program_write(b"chunk"))
