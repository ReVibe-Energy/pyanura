"""Opening AVSS clients over a transport."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pyanura.avss import AVSSClient
from pyanura.avss.transport import AVSSTransport


@asynccontextmanager
async def avss_client(transport: AVSSTransport) -> AsyncIterator[AVSSClient]:
    """Open `transport` and yield a client on it, closing it on exit."""
    async with transport:
        yield AVSSClient(transport)
