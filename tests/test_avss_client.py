"""Tests for how AVSSClient maps what its transport raises."""

import asyncio

import pytest

from pyanura.avss.client import AVSSClient
from pyanura.avss.exceptions import AVSSConnectionError, AVSSTransportError
from pyanura.avss.protocol import OpCode, ResponseCode
from pyanura.avss.transport.base import AVSSTransport


class FailingTransport(AVSSTransport):
    """Raises ``outcome`` from every control point request."""

    def __init__(self, outcome: BaseException):
        self.outcome = outcome

    async def open(self):
        pass

    async def close(self):
        pass

    async def control_point_request(self, req, *, timeout=None):
        raise self.outcome

    async def program_write(self, value):
        raise self.outcome

    def set_report_callback(self, callback):
        pass

    def set_program_callback(self, callback):
        pass

    def set_closed_callback(self, callback):
        pass


@pytest.mark.parametrize(
    "outcome",
    [AVSSTransportError("transport said so"), AVSSConnectionError("gone")],
    ids=["transport-error", "connection-error"],
)
def test_request_passes_the_transports_own_exceptions_through(outcome):
    client = AVSSClient(FailingTransport(outcome))

    with pytest.raises(type(outcome)) as excinfo:
        asyncio.run(client.get_version())

    assert excinfo.value is outcome


def test_request_wraps_other_exceptions_as_transport_errors():
    cause = RuntimeError("something below")
    client = AVSSClient(FailingTransport(cause))

    with pytest.raises(AVSSTransportError, match="Request failed") as excinfo:
        asyncio.run(client.get_version())

    assert excinfo.value.__cause__ is cause


class RecordingTransport(AVSSTransport):
    """Answers every control point request with OK, recording the requests."""

    def __init__(self):
        self.requests: list[bytes] = []
        self.events: list[str] = []

    async def open(self):
        self.events.append("open")

    async def close(self):
        self.events.append("close")

    async def control_point_request(self, req, *, timeout=None):
        self.requests.append(bytes(req))
        return bytes([OpCode.RESPONSE, req[0], ResponseCode.OK])

    async def program_write(self, value):
        raise NotImplementedError

    def set_report_callback(self, callback):
        pass

    def set_program_callback(self, callback):
        pass

    def set_closed_callback(self, callback):
        pass


def test_transport_is_an_async_context_manager():
    transport = RecordingTransport()

    async def scenario():
        async with transport as entered:
            assert entered is transport
            assert transport.events == ["open"]
        assert transport.events == ["open", "close"]

    asyncio.run(scenario())
