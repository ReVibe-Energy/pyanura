import anura.avss as avss
import asyncio
from bleak import BleakClient, BleakError
import logging

from . import (
    AVSSClient
)

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
        def report_notify(sender, data):
            self._on_report_notify(data)
        def program_notify(sender, data):
            self._on_program_notify(data)
        await self.client.connect()
        await self.client.start_notify(avss.ReportCharacteristicUuid, report_notify)
        await self.client.start_notify(avss.ControlPointCharacteristicUuid, self._cp_indicate)
        await self.client.start_notify(avss.ProgramCharacteristicUuid, program_notify)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.disconnect()

    def _cp_indicate(self, sender, data):
            self._cp_response_q.put_nowait(data)

    async def _request_raw(self, req, timeout=5.0):
        """Write to the control point"""
        while not self._cp_response_q.empty():
            logger.warning("Flushing lingering responses")
            await self._cp_response_q.get()
            self._cp_response_q.task_done()
        await self.client.write_gatt_char(avss.ControlPointCharacteristicUuid, req)
        return await asyncio.wait_for(self._cp_response_q.get(), timeout=timeout)

    async def _program_write(self, value):
            return await self.client.write_gatt_char(avss.ProgramCharacteristicUuid, value, response=False)
