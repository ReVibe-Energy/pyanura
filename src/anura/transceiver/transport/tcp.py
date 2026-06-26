import asyncio
import struct
from dataclasses import dataclass

from .base import Transport, TransportInfo


@dataclass(frozen=True)
class TCPTransportInfo(TransportInfo):
    """Connection info for a TCP transport."""

    #: Resolved remote endpoint ``(host, port)``, or ``None`` if not connected.
    peer_address: tuple[str, int] | None


class TCPTransport(Transport, transport_type="tcp"):
    """
    TCP/IP based transceiver transport.
    """

    def __init__(self, hostname: str, port: str) -> None:
        self._hostname = hostname
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def open_connection(self):
        self._reader, self._writer = await asyncio.open_connection(
            self._hostname, self._port
        )

    async def send(self, payload):
        assert self._writer is not None, "Not connected"
        self._writer.write(struct.pack(">H", len(payload)))
        self._writer.write(payload)
        await self._writer.drain()

    async def read(self):
        assert self._reader is not None, "Not connected"
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

    def get_transport_info(self) -> TCPTransportInfo:
        return TCPTransportInfo(peer_address=self._peer_address())

    def _peer_address(self) -> tuple[str, int] | None:
        if self._writer is None:
            return None
        peer = self._writer.get_extra_info("peername")
        if peer is None:
            return None
        return peer[0], peer[1]
