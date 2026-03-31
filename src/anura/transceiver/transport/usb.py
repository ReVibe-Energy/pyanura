import asyncio
import errno
import logging
import struct
from typing import List, Optional

import usb.core
import usb.util

from .base import Transport

logger = logging.getLogger(__name__)

# The IDs used by our transceivers
VENDOR_ID = 0x16D0
PRODUCT_ID = 0x13D4

OUT_ENDPOINT = 0x01  # host to device
IN_ENDPOINT = 0x81  # device to host
MAX_PACKET_SIZE = 64

EOF_SENTINEL = object()  # end of the receive queue


class USBTransport(Transport, transport_type="usb"):
    """
    Connects to a USB transceiver identified by its serial number.
    """

    def __init__(self, serial_number: str, _unused_port) -> None:
        self.serial_number = serial_number
        self.vendor_id = VENDOR_ID
        self.product_id = PRODUCT_ID
        self.in_ep = IN_ENDPOINT
        self.out_ep = OUT_ENDPOINT
        self.max_packet_size = MAX_PACKET_SIZE
        self.dev: Optional[usb.core.Device] = None
        self.receive_queue: asyncio.Queue = asyncio.Queue()
        self.loop = asyncio.get_event_loop()
        self.reader_task: Optional[asyncio.Task] = None

    async def open_connection(self) -> None:
        device = await self.loop.run_in_executor(
            None, self._find_device_by_serial, self.serial_number
        )
        if device is None:
            raise ValueError(
                f"USB device with serial number '{self.serial_number}' not found"
            )

        logger.debug(f"Transceiver found: {device}")
        self.dev = device

        # Set the configuration. See
        # https://libusb.sourceforge.io/api-1.0/libusb_caveats.html#configsel
        self.dev.set_configuration()

        # Get rid of the kernel driver (if there is one)
        await self.loop.run_in_executor(None, self._detach_kernel_driver)

        # Get rid of old data on the IN endpoint that may be buffered
        # in the device.
        await self.flush_in_endpoint()

        # Start the background reader task that puts messages on the
        # receive queue
        self.reader_task = asyncio.create_task(self._background_reader())

    async def send(self, msg: bytes) -> None:
        assert self.dev is not None, "Not connected"

        if len(msg) > 0xFFFF:
            raise ValueError("Message too large", len(msg))
        packet = struct.pack(">H", len(msg)) + msg

        # Send the message to the device, with a timeout
        await self.loop.run_in_executor(None, self.dev.write, self.out_ep, packet, 1000)
        logger.debug(f"Sent message: {msg}")

    async def read(self) -> bytes:
        assert self.dev is not None, "Not connected"

        message = await self.receive_queue.get()
        if message is EOF_SENTINEL:
            logger.error("USB connection closed during read")
            raise asyncio.IncompleteReadError(partial=b"", expected=1)

        logger.debug(f"Dequeued message: {message}")

        return message

    async def close(self) -> None:
        if self.dev is None:
            return

        dev = self.dev
        self.dev = None

        if self.reader_task:
            self.reader_task.cancel()
            try:
                await self.reader_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error while cancelling reader task: {e}")

        usb.util.release_interface(dev, 0)
        usb.util.dispose_resources(dev)
        logger.debug("USB interface released")

        await self.receive_queue.put(EOF_SENTINEL)

    @staticmethod
    def list_devices() -> List[str]:
        """Get a list of serial numbers of connected transceivers."""
        devices = usb.core.find(find_all=True, idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        device_list = []
        for device in devices:
            serial = usb.util.get_string(device, device.iSerialNumber)
            if serial:
                device_list.append(serial)

        return device_list

    def _detach_kernel_driver(self):
        # These operations are not available on Windows and cause
        # NotImplementedError. See libusb docs:
        # https://libusb.sourceforge.io/api-1.0/group__libusb__dev.html#ga1cabd4660a274f715eeb82de112e0779
        try:
            if self.dev.is_kernel_driver_active(0):
                self.dev.detach_kernel_driver(0)
                logger.debug("Kernel driver detached")
        except NotImplementedError:
            logger.debug("Couldn't detach kernel driver (expected on Windows)")

    def _find_device_by_serial(self, serial_number: str) -> Optional[usb.core.Device]:
        devices = usb.core.find(
            find_all=True, idVendor=self.vendor_id, idProduct=self.product_id
        )
        for device in devices:
            dev_serial = usb.util.get_string(device, device.iSerialNumber)
            if dev_serial == serial_number:
                return device

    async def flush_in_endpoint(self) -> None:
        while True:
            try:
                _data = await self.loop.run_in_executor(
                    None, self.dev.read, self.in_ep, self.max_packet_size, 50
                )
            except usb.core.USBError as e:
                if hasattr(e, "errno") and e.errno == errno.ETIMEDOUT:
                    break
                else:
                    logger.error(f"Error while flushing IN endpoint: {e}")
                    raise

    async def _background_reader(self) -> None:
        # Task to always have a read pending on the IN endpoint
        buf = bytearray()
        while self.dev is not None:
            try:
                data = await self.loop.run_in_executor(
                    None, self.dev.read, self.in_ep, self.max_packet_size, 0
                )
                buf.extend(data)
                logger.debug(f"Received raw data: {data}")

                while True:
                    if len(buf) < 2:
                        break  # need more data

                    msg_length = struct.unpack(">H", buf[:2])[0]
                    total_length = 2 + msg_length

                    if len(buf) < total_length:
                        break  # need more data

                    # Pass on the CBOR payload
                    msg = bytes(buf[2:total_length])
                    buf = buf[total_length:]
                    logger.debug(f"Received payload: {msg}")
                    await self.receive_queue.put(msg)

            except usb.core.USBError as e:
                logger.error(f"USB Error while receiving: {e}")
                await self.close()
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in background reader: {e}")
                await self.close()
                break
