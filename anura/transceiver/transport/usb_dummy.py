import logging

from .base import Transport

logger = logging.getLogger(__name__)

class USBTransport(Transport, transport_type="usb"):
    """
    Dummy replacement for the USBTransport class in case the pyusb
    module is missing.
    """

    def __init__(self, serial_number: str, _unused_port) -> None:
        raise RuntimeError("Can't instantiate USBTransport due a missing module.")

    @staticmethod
    def list_devices() -> list[str]:
        return []

    async def open_connection(self):
        pass

    async def send(self, payload):
        pass

    async def read(self):
        pass

    async def close(self):
        pass
