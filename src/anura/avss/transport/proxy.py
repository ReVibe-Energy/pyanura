import asyncio
import enum
import logging

from anura.avss.exceptions import AVSSConnectionError, AVSSTransportError
from anura.transceiver.client import TransceiverClient
from anura.transceiver.exceptions import (
    TransceiverConnectionError,
    TransceiverRequestError,
)
from anura.transceiver.models import (
    APIErrorCode,
    AVSSProgramNotifiedEvent,
    AVSSReportNotifiedEvent,
    BluetoothAddrLE,
    NodeDisconnectedEvent,
)

from .base import AVSSTransport

logger = logging.getLogger(__name__)

# Seconds between polls of a node that is not yet available.
_POLL_INTERVAL = 1.0

# Bound on a readiness poll. A node that is connected and ready answers a
# GET_VERSION well inside this; one that does not is not about to.
_POLL_TIMEOUT = 5.0


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
        # TODO: This will wait indefinitely if transceiver is not assigned to
        # the node; a sanity check would be good.

        # NODE_UNAVAILABLE is expected while the transceiver is still
        # connecting to the node. BUSY means the transceiver has a request
        # for the node outstanding from elsewhere: it should clear once that
        # request finishes, or it may be stuck. Either way it is for the caller
        # to deal with, so it fails open() at once. A limited count of other
        # errors is tolerated.

        other_error_count = 0

        while True:
            try:
                get_version_request = b"\x05"  # GET_VERSION opcode
                await self._node_request(get_version_request, _POLL_TIMEOUT)
                return
            except TransceiverRequestError as e:
                if e.error.code == APIErrorCode.NODE_UNAVAILABLE:
                    other_error_count = 0
                elif e.error.code == APIErrorCode.BUSY:
                    raise AVSSConnectionError(
                        f"Transceiver reports {self._address} node as busy"
                    ) from None
                else:
                    logger.debug(
                        f"Unexpected error while waiting for {self._address} to become available: {e.error}"
                    )
                    other_error_count += 1
                    if other_error_count >= 3:
                        raise AVSSConnectionError(
                            f"Transceiver reported an error when polling for node: {e.error}"
                        ) from e
            except TimeoutError as e:
                raise AVSSConnectionError(
                    f"Node {self._address} did not answer when polled"
                ) from e

            await asyncio.sleep(_POLL_INTERVAL)

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
                        break  # connection broken

    async def _node_request(self, req: bytes, timeout: float | None) -> bytes:
        """Send a request to the node, bounded by ``timeout``.

        Raises:
            TimeoutError: If the node did not answer within ``timeout``.
        """
        if self._transceiver.supports_avss_request_timeout:
            # Pass timeout to let the transceiver enforce it.
            result = await self._transceiver.avss_request(
                self._address, req, timeout=timeout
            )
        else:
            # Fallback to a local timeout. This will leave the transceiver's
            # connection to this node in a broken state, surfaced as all
            # subsequent avss_request attempts failing until the BLE
            # connection is broken and re-established.
            async with asyncio.timeout(timeout):
                result = await self._transceiver.avss_request(
                    self._address, req, timeout=None
                )
        return result.response

    async def control_point_request(
        self, req: bytes, *, timeout: float | None = None
    ) -> bytes:
        if self._state is _State.CREATED:
            raise RuntimeError("Transport has not been opened")

        if self._state is _State.CLOSED:
            raise AVSSConnectionError("Connection has been closed")

        try:
            return await self._node_request(req, timeout)
        except TimeoutError:
            # The node took the request and did not answer it. Responses are
            # matched by order, so an unanswered request leaves the protocol
            # in a broken state. The transceiver will have either closed the
            # connection, or is waiting indefinitely for the node to answer;
            # in either case this transport is defunct.
            await self.close()
            raise
        except TransceiverRequestError as e:
            if e.error.code == APIErrorCode.NODE_UNAVAILABLE:
                raise AVSSConnectionError(
                    "Node not available via transceiver"
                ) from None
            raise
        except TransceiverConnectionError as e:
            raise AVSSConnectionError(f"Transceiver connection broken: {e}") from e

    async def program_write(self, value: bytes) -> None:
        if self._state is _State.CREATED:
            raise RuntimeError("Transport has not been opened")

        if self._state is _State.CLOSED:
            raise AVSSConnectionError("Connection has been closed")

        try:
            # A TimeoutError from the transceiver means it gave up on getting
            # the write into its TX path in time; the write was never sent
            # and the node is still connected, so it passes through for the
            # caller to retry.
            await self._transceiver.avss_program_write(self._address, value)
        except TransceiverRequestError as e:
            if e.error.code == APIErrorCode.NODE_UNAVAILABLE:
                raise AVSSConnectionError(
                    "Node not available via transceiver"
                ) from None
            raise AVSSTransportError(f"Program write failed: {e}") from e
        except TransceiverConnectionError as e:
            raise AVSSConnectionError(f"Transceiver connection broken: {e}") from e

    def set_report_callback(self, callback) -> None:
        self._report_callback = callback

    def set_program_callback(self, callback) -> None:
        self._program_callback = callback

    def set_closed_callback(self, callback) -> None:
        self._closed_callback = callback
