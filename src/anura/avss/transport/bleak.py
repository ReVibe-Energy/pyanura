import asyncio
import logging

from bleak import BleakClient  # pyright: ignore[reportMissingImports]
from bleak.exc import BleakError  # pyright: ignore[reportMissingImports]

import anura.avss as avss
from anura.avss.exceptions import AVSSConnectionError, AVSSTransportError

from .base import AVSSTransport

logger = logging.getLogger(__name__)


class BleakAVSSTransport(AVSSTransport):
    """AVSS transport using Bleak for direct BLE communication.

    This transport manages a BLE connection to an AVSS device and handles
    notifications from the Report, Program, and Control Point characteristics.
    """

    def __init__(self, addr):
        """Initialize the transport.

        Args:
            addr: BLE address or device identifier for the AVSS device
        """
        self._addr = addr
        self._client = None
        self._closed_event = asyncio.Event()
        # Response slot for the outstanding control point request, if any.
        self._cp_response: asyncio.Future[bytes] | None = None
        self._report_callback = None
        self._program_callback = None
        self._closed_callback = None

    async def open(self) -> None:
        """Connect to the BLE device and start notifications."""
        if self._client is not None:
            raise RuntimeError("BleakAVSSTransport is already open")

        def disconnected_callback(client: BleakClient):
            response, self._cp_response = self._cp_response, None
            if response is not None and not response.done():
                response.set_exception(
                    AVSSConnectionError("Connection has been closed")
                )
            self._closed_event.set()

            if self._closed_callback:
                self._closed_callback()

        def report_notify(sender, data):
            if self._report_callback:
                self._report_callback(data)

        def program_notify(sender, data):
            if self._program_callback:
                self._program_callback(data)

        def cp_indicate(sender, data):
            response, self._cp_response = self._cp_response, None
            if response is None:
                logger.debug("Control Point response with no request outstanding")
            elif not response.done():
                response.set_result(data)

        self._client = BleakClient(
            self._addr, disconnected_callback=disconnected_callback
        )

        try:
            await self._client.connect()
            await self._client.start_notify(
                avss.uuids.ReportCharacteristicUuid, report_notify
            )
            await self._client.start_notify(
                avss.uuids.ControlPointCharacteristicUuid, cp_indicate
            )
            await self._client.start_notify(
                avss.uuids.ProgramCharacteristicUuid, program_notify
            )
        except BaseException as e:
            # Tear down on any failure so close() can't hang on a half-open
            # client or leak a live connection.
            try:
                await self._client.disconnect()
            except BleakError:
                pass
            self._client = None
            if isinstance(e, BleakError):
                raise AVSSConnectionError(str(e)) from e
            raise

    async def close(self) -> None:
        """Disconnect from the BLE device."""
        if not self._client:
            return

        try:
            await self._client.disconnect()
        except EOFError:
            # On some platforms EOFError is raised by _client.disconnect()
            # even after disconnected callback has been called, so we suppress it
            pass
        except BleakError as e:
            raise AVSSTransportError(str(e)) from e

        await self._closed_event.wait()

    def _discard(self, response: asyncio.Future[bytes]) -> None:
        """Drop a response that nobody is going to wait for."""
        if self._cp_response is response:
            self._cp_response = None

        if not response.cancel() and not response.cancelled():
            response.exception()  # retrieve exception to prevent asyncio warning

    async def control_point_request(
        self, req: bytes, *, timeout: float | None = None
    ) -> bytes:
        if self._client is None:
            raise RuntimeError("BleakAVSSTransport is not open")

        if self._cp_response is not None:
            raise AVSSTransportError(
                "A control point request is already outstanding on this device"
            )

        response = asyncio.get_running_loop().create_future()
        self._cp_response = response

        try:
            async with asyncio.timeout(timeout):
                try:
                    await self._client.write_gatt_char(
                        avss.uuids.ControlPointCharacteristicUuid, req
                    )
                except BleakError as e:
                    self._discard(response)

                    # The write may have gone out so the safe option is to close.
                    try:
                        await self.close()
                    except AVSSTransportError:
                        logger.debug(
                            "Could not close the transport after GATT write error"
                        )

                    raise AVSSConnectionError(
                        f"Control Point write failed: {e!s}"
                    ) from e

                return await response
        except TimeoutError:
            # The node took the request and did not answer it. Responses are
            # matched by order, so an unanswered request leaves the protocol
            # in a broken state. We have to close the connection.
            try:
                await self.close()
            except AVSSTransportError:
                logger.debug("Could not close the transport after a timeout")
            raise

    async def program_write(self, value: bytes) -> None:
        if self._client is None:
            raise RuntimeError("BleakAVSSTransport is not open")

        try:
            await self._client.write_gatt_char(
                avss.uuids.ProgramCharacteristicUuid, value, response=False
            )
        except BleakError as e:
            raise AVSSConnectionError(str(e)) from e

    def set_report_callback(self, callback) -> None:
        self._report_callback = callback

    def set_program_callback(self, callback) -> None:
        self._program_callback = callback

    def set_closed_callback(self, callback) -> None:
        self._closed_callback = callback
