import asyncio
import logging
from contextlib import contextmanager
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Generator,
    TypeVar,
    overload,
)

import cbor2

from anura.marshalling import marshal, unmarshal

from . import models
from .transport import Transport

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ProtocolError(Exception):
    "Raised when an invalid payload is received"

    pass


class RequestError(Exception):
    "Raised when a request returns an error"

    def __init__(self, method, error):
        self.error = error
        super().__init__(f'Request "{method}" returned an error response: {error}')


class TransceiverClientError(Exception):
    pass


class TransceiverClient:
    def __init__(self, target_spec: str, port: int = 7645) -> None:
        self._transport = Transport.create(target_spec, port)
        self._pending_responses = {}
        self._known_methods = {}
        self._disconnected = asyncio.Future()
        self._connection_task: asyncio.Task = None
        self._on_notification_callbacks: list[
            Callable[[models.Notification], None]
        ] = []

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        logger.debug("Closing connection")
        if self._connection_task:
            self._connection_task.cancel()
        await self._transport.close()

    async def connect(self):
        logger.debug("Connecting to %s", self._transport)
        await self._transport.open_connection()
        logger.debug("Connected")

        async def recv_task():
            while True:
                payload = None
                try:
                    payload = await self._transport.read()
                except asyncio.IncompleteReadError:
                    break
                message = cbor2.loads(payload)
                match message:
                    case [models.msg_type.Response, request_token, error, result]:
                        response = self._pending_responses.pop(request_token, None)
                        if response and response.cancelled():
                            logger.warning("Response to cancelled request received")
                        elif response:
                            response.set_result([error, result])
                    case [models.msg_type.Notification, type_, argument]:
                        n = models.Notification.parse(type_, argument)
                        for callback in self._on_notification_callbacks:
                            callback(n)
                    case _:
                        raise ProtocolError()

        async def ping_task():
            """Task to keep the connection alive."""
            while True:
                await asyncio.sleep(1.0)
                await self.ping()

        async def conn_tasks():
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(recv_task())
                    tg.create_task(ping_task())
            finally:
                self._disconnected.set_result(None)
                logger.debug("Connection closed")

        self._connection_task = asyncio.create_task(conn_tasks())

        # Discover methods automatically
        await self.discover_methods()

    async def _send(self, payload):
        await self._transport.send(payload)

    async def _request_internal(self, method, param):
        method_id = method
        if method in self._known_methods:
            method_id = self._known_methods[method]

        request_token = 0
        # Find lowest unused request token
        while request_token in self._pending_responses:
            request_token += 1
        loop = asyncio.get_event_loop()
        response = loop.create_future()
        self._pending_responses[request_token] = response
        await self._send(
            cbor2.dumps([models.msg_type.Request, request_token, method_id, param])
        )
        match await response:
            case [None, result]:
                return result
            case [error, _]:
                raise RequestError(method, error)

    @overload
    async def request(
        self, method: str, arg: Any = None, /, *, result_type: type[T]
    ) -> T: ...

    @overload
    async def request(self, method: str, arg: Any = None, /) -> Any: ...

    async def request(self, method, arg=None, result_type=None):
        "Send a send a request and receive the response"
        result = await self._request_internal(method, marshal(arg))
        if result_type:
            return unmarshal(result_type, result)
        else:
            return result

    def _callback_and_generator(
        self,
    ) -> tuple[
        Callable[[models.Notification], None], AsyncGenerator[models.Notification, None]
    ]:
        # Queue to hold the incoming notifications
        notifications: asyncio.Queue[models.Notification] = asyncio.Queue()

        def _callback(msg: models.Notification) -> None:
            """Put the new notification in the queue."""
            try:
                notifications.put_nowait(msg)
            except asyncio.QueueFull:
                self._logger.warning("Notification queue is full. Discarding message.")

        async def _generator() -> AsyncGenerator[models.Notification, None]:
            """Forward all notifications from the notification queue."""
            while True:
                # Wait until we either:
                #  1. Receive a notification
                #  2. Disconnect from the transceiver
                loop = asyncio.get_running_loop()
                get: asyncio.Task[models.Message] = loop.create_task(
                    notifications.get()
                )
                try:
                    done, _ = await asyncio.wait(
                        (get, self._disconnected), return_when=asyncio.FIRST_COMPLETED
                    )
                except asyncio.CancelledError:
                    # If the asyncio.wait is cancelled, we must make sure
                    # to also cancel the underlying tasks.
                    get.cancel()
                    raise
                if get in done:
                    # We received a notification. Return the result.
                    yield get.result()
                else:
                    # We got disconnected from the broker. Cancel the "get" task.
                    get.cancel()
                    # Stop the generator with the following exception
                    msg = "Disconnected during notification iteration"
                    raise TransceiverClientError(msg)

        return _callback, _generator()

    @contextmanager
    def notifications(
        self,
    ) -> Generator[AsyncGenerator[models.Notification, None], None, None]:
        """Context manager that creates a queue for incoming notifications.

        Returns:
            An async generator that yields messages from the underlying queue.
        """
        callback, generator = self._callback_and_generator()
        try:
            # Add to the list of callbacks to call when a message is received
            self._on_notification_callbacks.append(callback)
            # Back to the caller (run whatever is inside the with statement)
            yield generator
        finally:
            # We are exiting the with statement. Remove the callback from the list.
            self._on_notification_callbacks.remove(callback)

    async def discover_methods(self):
        self._known_methods = await self.request(".well-known/methods", None)
        return self._known_methods

    async def reboot(self):
        return await self.request("reboot")

    async def dfu_prepare(self, size: int):
        args = models.DfuPrepareArgs(size=size)
        return await self.request("dfu_prepare", args)

    async def dfu_write(self, offset: int, data: bytes):
        args = models.DfuWriteArgs(offset=offset, data=data)
        return await self.request("dfu_write", args)

    async def dfu_write_image(self, image: bytes, chunk_size=300):
        offset = 0
        while offset < len(image):
            if offset + chunk_size < len(image):
                chunk = image[offset : (offset + chunk_size)]
            else:
                chunk = image[offset:]
            logger.info(
                f"Writing image offset={offset} ({int(offset / len(image) * 100)}%)"
            )
            await self.dfu_write(offset, chunk)
            offset += len(chunk)

    async def dfu_apply(self, permanent=False):
        if permanent:
            args = models.DfuApplyArgs(permanent=0x5045524D)  # ASCII "PERM"
        else:
            args = models.DfuApplyArgs(permanent=0)
        return await self.request("dfu_apply", args)

    async def dfu_confirm(self):
        return await self.request("dfu_confirm")

    async def set_assigned_nodes(self, addrs: list[models.BluetoothAddrLE]):
        nodes = [models.AssignedNode(address=addr) for addr in addrs]
        args = models.SetAssignedNodesArgs(nodes=nodes)
        return await self.request("set_assigned_nodes", args)

    async def get_assigned_nodes(self) -> models.GetAssignedNodesResult:
        return await self.request(
            "get_assigned_nodes", result_type=models.GetAssignedNodesResult
        )

    async def get_connected_nodes(self) -> models.GetConnectedNodesResult:
        return await self.request(
            "get_connected_nodes", result_type=models.GetConnectedNodesResult
        )

    async def get_device_info(self) -> models.GetDeviceInfoResult:
        return await self.request(
            "get_device_info", result_type=models.GetDeviceInfoResult
        )

    async def get_device_status(self) -> models.GetDeviceStatusResult:
        return await self.request(
            "get_device_status", result_type=models.GetDeviceStatusResult
        )

    async def get_firmware_info(self) -> models.GetFirmwareInfoResult:
        return await self.request(
            "get_firmware_info", result_type=models.GetFirmwareInfoResult
        )

    async def get_ptp_status(self) -> models.GetPtpStatusResult:
        return await self.request(
            "get_ptp_status", result_type=models.GetPtpStatusResult
        )

    async def set_time(self, time: int):
        args = models.SetTimeArgs(time=time)
        return await self.request("set_time", args)

    async def get_time(self) -> models.GetTimeResult:
        return await self.request("get_time", result_type=models.GetTimeResult)

    async def scan_nodes(self):
        return await self.request("scan_nodes")

    async def ping(self, arg=None):
        # arg is ignored by server
        return await self.request("ping", arg)

    async def slow_ping(self):
        return await self.request("slow_ping")

    async def scan_nodes_stop(self):
        return await self.request("scan_nodes_stop")

    async def avss_request(self, addr: models.BluetoothAddrLE, data: bytes):
        args = models.AVSSRequestArgs(address=addr, data=data)
        return await self.request("avss_request", args)

    async def avss_program_write(self, addr: models.BluetoothAddrLE, data: bytes):
        args = models.AVSSProgramWriteArgs(address=addr, data=data)
        return await self.request("avss_program_write", args)

    async def find_avss_node_by_address(self, addr: models.BluetoothAddrLE):
        with self.notifications() as notifications:
            assigned_nodes = await self.get_assigned_nodes()
            is_assigned = any(node.address == addr for node in assigned_nodes.nodes)
            if not is_assigned:
                return None  # Not assigned, so we can just give up

            connected_nodes = await self.get_connected_nodes()
            is_connected = any(node.address == addr for node in connected_nodes.nodes)
            if is_connected:
                return addr  # Already connected

            async for msg in notifications:
                if (
                    isinstance(msg, models.NodeServiceDiscoveredEvent)
                    and msg.address == addr
                ):
                    return addr
