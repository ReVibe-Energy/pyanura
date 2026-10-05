"""Tests for how AVSSClient maps what its transport raises."""

import asyncio

import pytest

from anura.avss.client import AVSSClient
from anura.avss.exceptions import AVSSConnectionError, AVSSTransportError
from anura.avss.transport.base import AVSSTransport


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
