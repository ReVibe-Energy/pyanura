"""AVSS transport layer implementations.

This package provides different transport implementations for AVSS communication:
- BleakAVSSTransport: Direct BLE communication using the bleak library
- ProxyAVSSTransport: Proxied communication via a Transceiver

The BleakAVSSTransport requires the optional 'bleak' dependency.
"""

from .base import AVSSTransport
from .proxy import ProxyAVSSTransport

__all__ = ["AVSSTransport", "ProxyAVSSTransport"]

# Conditionally import BleakAVSSTransport if bleak is available
try:
    from .bleak import BleakAVSSTransport as BleakAVSSTransport

    __all__.append("BleakAVSSTransport")
    HAS_BLEAK = True
except ImportError:
    HAS_BLEAK = False


def __getattr__(name):
    """Provide helpful error message when bleak is not available."""
    if name == "BleakAVSSTransport":
        if not HAS_BLEAK:
            raise ImportError(
                "BleakAVSSTransport requires the 'bleak' package."
            ) from None
        else:
            # This should be unreachable as we only expect __getattr__ to be
            # called when BleakAVSSTransport is not in fact available.
            from .bleak import BleakAVSSTransport

            return BleakAVSSTransport
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
