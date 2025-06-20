import asyncio
import logging

from bleak import BleakClient

import anura.avss as avss

from . import AVSSClient

logger = logging.getLogger(__name__)


class BleakAVSSClient(AVSSClient):
    def __init__(self, addr):
        self._cp_response_q = asyncio.Queue(maxsize=1)
        self._disconnected = asyncio.Future()

        def disconnected(client: BleakClient):
            self._disconnected.set_result(None)

        self.client = BleakClient(addr, disconnected_callback=disconnected)
        super().__init__()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.disconnect()

    async def connect(self):
        def report_notify(sender, data):
            self._on_report_notify(data)

        def program_notify(sender, data):
            self._on_program_notify(data)

        await self.client.connect()
        await self.client.start_notify(
            avss.uuids.ReportCharacteristicUuid, report_notify
        )
        await self.client.start_notify(
            avss.uuids.ControlPointCharacteristicUuid, self._cp_indicate
        )
        await self.client.start_notify(
            avss.uuids.ProgramCharacteristicUuid, program_notify
        )

    async def disconnect(self):
        await self.client.disconnect()

    def _cp_indicate(self, sender, data):
        self._cp_response_q.put_nowait(data)

    async def _request_raw(self, req, timeout=5.0):
        """Write to the control point"""
        while not self._cp_response_q.empty():
            logger.warning("Flushing lingering responses")
            await self._cp_response_q.get()
            self._cp_response_q.task_done()
        await self.client.write_gatt_char(
            avss.uuids.ControlPointCharacteristicUuid, req
        )
        return await asyncio.wait_for(self._cp_response_q.get(), timeout=timeout)

    async def _program_write(self, value):
        return await self.client.write_gatt_char(
            avss.uuids.ProgramCharacteristicUuid, value, response=False
        )
