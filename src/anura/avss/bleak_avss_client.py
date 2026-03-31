"""Compatibility shim for BleakAVSSClient using the new transport interface.

This module provides backward compatibility for code using the old BleakAVSSClient
class. New code should use BleakAVSSTransport directly.
"""

import warnings

import anura.avss as avss
from anura.avss.transport import BleakAVSSTransport


class BleakAVSSClient(avss.AVSSClient):
    """Compatibility wrapper for BleakAVSSClient.

    This class maintains backward compatibility with the old interface while
    using the new transport layer internally.

    Deprecated: Use BleakAVSSTransport with AVSSClient directly instead.
    """

    def __init__(self, addr):
        """Initialize BleakAVSSClient.

        Args:
            addr: BLE address or device identifier for the AVSS device

        Deprecated: Use BleakAVSSTransport and AVSSClient instead:
            transport = BleakAVSSTransport(addr)
            await transport.open()
            client = AVSSClient(transport)
        """
        warnings.warn(
            "BleakAVSSClient is deprecated. Use BleakAVSSTransport with AVSSClient instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._transport = BleakAVSSTransport(addr)
        super().__init__(self._transport)

    async def __aenter__(self):
        await self._transport.open()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._transport.close()
