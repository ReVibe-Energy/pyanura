import asyncio
import struct

from .base import Transport


class TCPTransport(Transport, transport_type="tcp"):
    """
    TCP/IP based transceiver transport.
    """

    def __init__(self, hostname: str, port: str) -> None:
        self._hostname = hostname
        self._port = port

    async def open_connection(self):
        self._reader, self._writer = await asyncio.open_connection(
            self._hostname, self._port
        )

    async def send(self, payload):
        self._writer.write(struct.pack(">H", len(payload)))
        self._writer.write(payload)
        await self._writer.drain()

    async def read(self):
        header = await self._reader.readexactly(2)
        (payload_len,) = struct.unpack(">H", header)
        payload = await self._reader.readexactly(payload_len)
        return payload

    async def close(self):
        if self._writer is None:
            return
        self._writer.close()
        await self._writer.wait_closed()
        self._writer = None
