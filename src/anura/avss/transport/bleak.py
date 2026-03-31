import asyncio
import logging

from bleak import BleakClient

import anura.avss as avss

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
        self._cp_response_q = asyncio.Queue(maxsize=1)
        self._report_callback = None
        self._program_callback = None
        self._closed_callback = None

    async def open(self) -> None:
        """Connect to the BLE device and start notifications."""
        if self._client is not None:
            raise RuntimeError("BleakAVSSTransport is already open")

        def disconnected_callback(client: BleakClient):
            self._closed_event.set()

        def report_notify(sender, data):
            if self._report_callback:
                self._report_callback(data)

        def program_notify(sender, data):
            if self._program_callback:
                self._program_callback(data)

        def cp_indicate(sender, data):
            self._cp_response_q.put_nowait(data)

        self._client = BleakClient(
            self._addr, disconnected_callback=disconnected_callback
        )

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
        finally:
            self._client = None
            self._closed_event.set()

    async def control_point_request(self, req: bytes) -> bytes:
        if self._client is None:
            raise RuntimeError("BleakAVSSTransport is not open")

        # Flush any lingering responses
        while not self._cp_response_q.empty():
            logger.warning("Flushing lingering responses")
            await self._cp_response_q.get()
            self._cp_response_q.task_done()

        await self._client.write_gatt_char(
            avss.uuids.ControlPointCharacteristicUuid, req
        )

        response = await self._cp_response_q.get()
        self._cp_response_q.task_done()
        return response

    async def program_write(self, value: bytes) -> None:
        if self._client is None:
            raise RuntimeError("BleakAVSSTransport is not open")

        await self._client.write_gatt_char(
            avss.uuids.ProgramCharacteristicUuid, value, response=False
        )

    def set_report_callback(self, callback) -> None:
        self._report_callback = callback

    def set_program_callback(self, callback) -> None:
        self._program_callback = callback

    def set_closed_callback(self, callback) -> None:
        self._closed_callback = _closed_callback
