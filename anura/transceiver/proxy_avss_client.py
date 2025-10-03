import asyncio

import anura.avss as avss

from .models import (
    AVSSProgramNotifiedEvent,
    AVSSReportNotifiedEvent,
    BluetoothAddrLE,
    NodeDisconnectedEvent,
)


class ProxyAVSSClient(avss.AVSSClient):
    def __init__(self, transceiver, address):
        self.transceiver = transceiver
        self.address = address

        if type(address) is not BluetoothAddrLE:
            raise ValueError("Type of 'address' must be BluetoothAddrLE")

        super().__init__()

    async def __aenter__(self):
        async def loop():
            with self.transceiver.notifications() as notifications:
                try:
                    async for msg in notifications:
                        if (
                            isinstance(msg, AVSSReportNotifiedEvent)
                            and msg.address == self.address
                        ):
                            self._on_report_notify(msg.value)
                        elif (
                            isinstance(msg, AVSSProgramNotifiedEvent)
                            and msg.address == self.address
                        ):
                            self._on_program_notify(msg.value)
                        elif (
                            isinstance(msg, NodeDisconnectedEvent)
                            and msg.address == self.address
                        ):
                            return
                finally:
                    self._disconnected.set_result(None)

        self._loop_task = asyncio.create_task(loop())
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._loop_task.cancel()

    async def _request_raw(self, req, timeout):
        result = await self.transceiver.avss_request(self.address, req)
        return result[0]

    async def _program_write(self, value):
        await self.transceiver.avss_program_write(self.address, value)
