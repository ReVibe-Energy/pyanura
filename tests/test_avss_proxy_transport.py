"""Tests for the error mapping and node polling of ProxyAVSSTransport."""

import asyncio

import pytest

from anura.avss.exceptions import AVSSConnectionError, AVSSTransportError
from anura.avss.transport import proxy
from anura.avss.transport.proxy import ProxyAVSSTransport, _State
from anura.transceiver import models
from anura.transceiver.exceptions import (
    TransceiverConnectionError,
    TransceiverRequestError,
)

NODE = models.BluetoothAddrLE.parse("C0:00:00:00:00:01")


class FakeTransceiver:
    """Stands in for TransceiverClient.

    Program writes raise ``outcome``. GET_VERSION polls take their result
    from ``polls`` in turn: an exception is raised, a callable is called,
    anything else is returned. Once ``polls`` is exhausted they succeed.
    """

    def __init__(self, outcome: BaseException | None = None, polls=()):
        self.outcome = outcome
        self.polls = list(polls)
        self.poll_count = 0
        self.writes: list[bytes] = []

    async def avss_request(self, addr, req, *, timeout=None):
        assert addr == NODE
        assert req == b"\x05"  # GET_VERSION
        self.poll_count += 1
        if not self.polls:
            return None
        result = self.polls.pop(0)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return result()
        return result

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


@pytest.fixture
def fast_polling(monkeypatch):
    monkeypatch.setattr(proxy, "_POLL_INTERVAL", 0)


def node_unavailable() -> TransceiverRequestError:
    return request_error(models.APIErrorCode.NODE_UNAVAILABLE)


def operation_failed() -> TransceiverRequestError:
    return request_error(models.APIErrorCode.OPERATION_FAILED)


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


def test_open_polls_until_the_node_answers(fast_polling):
    transceiver = FakeTransceiver(polls=[node_unavailable()] * 4)
    transport = open_transport(transceiver)

    asyncio.run(transport._wait_available())

    assert transceiver.poll_count == 5


def test_open_tolerates_other_errors_between_node_unavailable(fast_polling):
    # NODE_UNAVAILABLE resets the count of other errors, which is capped at 3.
    transceiver = FakeTransceiver(
        polls=[
            operation_failed(),
            operation_failed(),
            node_unavailable(),
            operation_failed(),
            operation_failed(),
        ]
    )
    transport = open_transport(transceiver)

    asyncio.run(transport._wait_available())

    assert transceiver.poll_count == 6


def test_open_fails_when_the_node_takes_a_poll_but_does_not_answer(fast_polling):
    # Unlike an unavailable node, this one is reachable and broken. Retrying
    # only has the transceiver disconnect it again, so the first one is fatal.
    transceiver = FakeTransceiver(polls=[node_unavailable(), TimeoutError()])
    transport = open_transport(transceiver)

    with pytest.raises(AVSSConnectionError, match="did not answer"):
        asyncio.run(transport._wait_available())

    assert transceiver.poll_count == 2
