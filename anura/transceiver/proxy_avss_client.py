"""Compatibility shim for ProxyAVSSClient using the new transport interface.

This module provides backward compatibility for code using the old ProxyAVSSClient
class. New code should use ProxyAVSSTransport directly.
"""

import warnings

import anura.avss as avss
from anura.avss.transport import ProxyAVSSTransport


class ProxyAVSSClient(avss.AVSSClient):
    """Compatibility wrapper for ProxyAVSSClient.

    This class maintains backward compatibility with the old interface while
    using the new transport layer internally.

    Deprecated: Use ProxyAVSSTransport with AVSSClient directly instead.
    """

    def __init__(self, transceiver, address):
        """Initialize ProxyAVSSClient.

        Args:
            transceiver: The Transceiver instance to use for communication
            address: BluetoothAddrLE of the target device

        Deprecated: Use ProxyAVSSTransport and AVSSClient instead:
            transport = ProxyAVSSTransport(transceiver, address)
            await transport.open()
            client = AVSSClient(transport)
        """
        warnings.warn(
            "ProxyAVSSClient is deprecated. Use ProxyAVSSTransport with AVSSClient instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._transport = ProxyAVSSTransport(transceiver, address)
        super().__init__(self._transport)

    async def __aenter__(self):
        await self._transport.open()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._transport.close()
