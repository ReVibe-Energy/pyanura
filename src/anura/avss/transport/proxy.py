import asyncio
import enum
import logging

from anura.avss.exceptions import AVSSConnectionError
from anura.transceiver.client import TransceiverClient
from anura.transceiver.exceptions import TransceiverRequestError
from anura.transceiver.models import (
    APIErrorCode,
    AVSSProgramNotifiedEvent,
    AVSSReportNotifiedEvent,
    BluetoothAddrLE,
    NodeDisconnectedEvent,
)

from .base import AVSSTransport

logger = logging.getLogger(__name__)


class _State(enum.Enum):
    CREATED = "created"
    OPENED = "opened"
    CLOSED = "closed"


class ProxyAVSSTransport(AVSSTransport):
    """AVSS transport using a Transceiver for communication.

    This transport delegates AVSS communication to a Transceiver, which manages
    the actual BLE connections. The transport listens for notifications from
    the transceiver for a specific device address.
    """

    def __init__(self, transceiver: TransceiverClient, address: BluetoothAddrLE):
        """Initialize the transport.

        Args:
            transceiver: The Transceiver instance to use for communication
            address: BluetoothAddrLE of the target device
        """
        self._state = _State.CREATED

        self._transceiver = transceiver
        self._address = address
        self._loop_task: asyncio.Task | None = None
        self._report_callback = None
        self._program_callback = None
        self._closed_callback = None

    async def open(self) -> None:
        if self._state is not _State.CREATED:
            raise RuntimeError("Transport has already been opened")

        self._state = _State.OPENED
        self._loop_task = asyncio.create_task(self._transport_loop())
        self._loop_task.add_done_callback(self._on_closed)

        await self._wait_available()

    async def _wait_available(self):
        # TODO: This will wait indefintely if transceiver is not assigned to the node
        # a sanity check would be good.

        # We expect NODE_UNAVAILABLE errors while waiting for the node but tolerate
        # a limited count of other errors.

        other_error_count = 0

        while True:
            try:
                get_version_request = b"\x05"  # GET_VERSION opcode
                await self._transceiver.avss_request(self._address, get_version_request)
                break
            except TransceiverRequestError as e:
                if api_error := e.api_error():
                    if api_error.code == APIErrorCode.NODE_UNAVAILABLE:
                        other_error_count = 0
                    else:
                        logger.warning(
                            f"Unexpected error while waiting for {self._address} to become available: {api_error}"
                        )
                        other_error_count += 1
                        if other_error_count >= 3:
                            raise AVSSConnectionError(
                                f"Transceiver report an error when polling for node: {api_error}"
                            ) from e
                else:
                    raise  # failing to parse api error is never expected
            await asyncio.sleep(1.0)

    def _on_closed(self, task: asyncio.Task):
        assert self._state is _State.OPENED
        assert self._loop_task is task

        if not task.cancelled():
            task.exception()  # mark exception as retreived

        self._state = _State.CLOSED
        self._loop_task = None  # discard task reference

        if callback := self._closed_callback:
            callback()

    async def close(self) -> None:
        if self._state is _State.CREATED:
            raise RuntimeError("Transport has not been opened")

        if self._state is _State.CLOSED:
            return

        assert self._loop_task is not None

        self._loop_task.cancel()
        await asyncio.wait([self._loop_task])

        assert self._state is _State.CLOSED

    async def _transport_loop(self):
        with self._transceiver.notifications() as notifications:
            async for notification in notifications:
                match notification:
                    case AVSSReportNotifiedEvent(address=self._address):
                        if cb := self._report_callback:
                            asyncio.get_running_loop().call_soon(cb, notification.value)
                    case AVSSProgramNotifiedEvent(address=self._address):
                        if cb := self._program_callback:
                            asyncio.get_running_loop().call_soon(cb, notification.value)
                    case NodeDisconnectedEvent(address=self._address):
                        break  # connection

    async def control_point_request(self, req: bytes) -> bytes:
        if self._state is _State.CREATED:
            raise RuntimeError("Transport has not been opened")

        if self._state is _State.CLOSED:
            raise AVSSConnectionError("Connection has been closed")

        try:
            result = await self._transceiver.avss_request(self._address, req)
            return result.response
        except TransceiverRequestError as e:
            if api_error := e.api_error():
                if api_error.code == APIErrorCode.NODE_UNAVAILABLE:
                    raise AVSSConnectionError(
                        "Node not available via transceiver"
                    ) from None
            raise

    async def program_write(self, value: bytes) -> None:
        if self._state is _State.CREATED:
            raise RuntimeError("Transport has not been opened")

        if self._state is _State.CLOSED:
            raise AVSSConnectionError("Connection has been closed")

        await self._transceiver.avss_program_write(self._address, value)

    def set_report_callback(self, callback) -> None:
        self._report_callback = callback

    def set_program_callback(self, callback) -> None:
        self._program_callback = callback

    def set_closed_callback(self, callback) -> None:
        self._closed_callback = callback
