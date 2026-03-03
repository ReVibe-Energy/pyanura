import asyncio
import logging
from contextlib import contextmanager
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Generator,
    TypeVar,
    overload,
)

import cbor2

from anura.marshalling import marshal, unmarshal

from . import models
from .exceptions import (
    TransceiverConnectionError,
    TransceiverError,
    TransceiverRequestError,
)
from .transport import Transport

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TransceiverClient:
    def __init__(self, target_spec: str, port: int = 7645) -> None:
        self._transport = Transport.create(target_spec, port)
        self._pending_responses = {}
        self._known_methods: dict[str, int] = {}
        self._connection_task: asyncio.Task[None] | None = None
        self._connection_closed = asyncio.Event()
        self._connection_exception: BaseException | None = None
        self._notification_callbacks: list[Callable[[models.Notification], None]] = []
        self._next_request_token: int = 0

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.disconnect()

    async def _handle_connection(self) -> None:
        async def recv_task():
            while True:
                try:
                    message_bytes = await self._transport.read()
                except Exception as e:
                    raise TransceiverError("Transport read failed") from e

                try:
                    message = cbor2.loads(message_bytes)
                except cbor2.CBORDecodeError as e:
                    raise TransceiverError("Received an invalid CBOR payload") from e

                match message:
                    case [models.msg_type.Response, request_token, error, result]:
                        if response := self._pending_responses.get(request_token, None):
                            response.set_result((error, result))
                    case [models.msg_type.Notification, type_, argument]:
                        n = models.Notification.parse(type_, argument)
                        for callback in self._notification_callbacks:
                            callback(n)
                    case _:
                        raise TransceiverError("Received an invalid CBOR-RPC message")

        async def keep_alive():
            while True:
                await asyncio.sleep(1.0)

                try:
                    await asyncio.wait_for(self.ping(), 1.0)
                except TimeoutError:
                    raise TransceiverError("Keepalive ping timed out") from None

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(recv_task())
                tg.create_task(keep_alive())
        except* TransceiverError as eg:
            if len(eg.exceptions) == 1:
                raise eg.exceptions[0]
            else:
                raise
        finally:
            await self._transport.close()

    async def connect(self):
        if self._connection_task:
            raise RuntimeError("Client has already been connected")

        try:
            await self._transport.open_connection()
        except Exception as e:
            raise TransceiverConnectionError(
                f"Failed to connect: {type(e)}, {e}"
            ) from e

        self._connection_task = asyncio.create_task(self._handle_connection())
        self._connection_task.add_done_callback(self._on_disconnected)

        # Discover methods automatically
        await self.discover_methods()

    def _on_disconnected(self, task: asyncio.Task):
        assert task is self._connection_task

        if not task.cancelled():
            self._connection_exception = task.exception()

        self._connection_closed.set()

    async def disconnect(self) -> None:
        if not self._connection_task:
            raise RuntimeError("Client has not been connected")

        self._connection_task.cancel()
        await asyncio.wait([self._connection_task])

        assert self._connection_closed.is_set()

    async def wait_for_disconnection(self) -> None:
        if not self._connection_task:
            raise RuntimeError("Client has not been connected")
        await self._connection_closed.wait()

    async def _request_internal(
        self, method: str, param, *, timeout: float | None = None
    ):
        if not self._connection_task:
            raise RuntimeError("Client has not been connected")

        # Look up index for method name
        if method in self._known_methods:
            resolved_method = self._known_methods[method]
        else:
            resolved_method = method

        request_token = self._next_request_token
        self._next_request_token = (self._next_request_token + 1) & 0xFFFFFFFF

        try:
            response_fut = asyncio.get_running_loop().create_future()
            self._pending_responses[request_token] = response_fut

            payload = cbor2.dumps(
                [models.msg_type.Request, request_token, resolved_method, param]
            )
            await self._transport.send(payload)

            async with asyncio.Timeout(timeout):
                async with asyncio.TaskGroup() as tg:
                    monitor_task = tg.create_task(self._connection_closed.wait())
                    done, _ = await asyncio.wait(
                        [monitor_task, response_fut],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    monitor_task.cancel()
                    response_fut.cancel()

                if response_fut in done:
                    match response_fut.result():
                        case (None, result):
                            return result
                        case (error, _):
                            raise TransceiverRequestError(method, error)
                else:
                    if self._connection_exception:
                        raise TransceiverConnectionError(
                            f'Connection broken during "{method}" request: {self._connection_exception}'
                        ) from self._connection_exception
                    else:
                        raise TransceiverConnectionError(
                            f'Connection broken during "{method}" request'
                        ) from None

        finally:
            del self._pending_responses[request_token]

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
        Callable[[models.Notification], None], AsyncIterator[models.Notification]
    ]:
        queue: asyncio.Queue[models.Notification] = asyncio.Queue()

        def _callback(msg: models.Notification) -> None:
            queue.put_nowait(msg)

        async def _generator() -> AsyncIterator[models.Notification]:
            monitor_task = asyncio.create_task(self._connection_closed.wait())
            try:
                while True:
                    get_task = asyncio.create_task(queue.get())
                    try:
                        done, _ = await asyncio.wait(
                            [monitor_task, get_task],
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        if get_task in done:
                            yield get_task.result()
                        else:
                            if self._connection_exception:
                                raise TransceiverConnectionError(
                                    f"Connection broken during notification iteration: {self._connection_exception}"
                                ) from self._connection_exception
                            else:
                                raise TransceiverConnectionError(
                                    "Connection broken during notification iteration"
                                ) from None
                    finally:
                        get_task.cancel()
                        await asyncio.wait([get_task])
            finally:
                monitor_task.cancel()
                await asyncio.wait([monitor_task])

        return _callback, _generator()

    @contextmanager
    def notifications(
        self,
    ) -> Generator[AsyncIterator[models.Notification], None, None]:
        """Context manager that creates a queue for incoming notifications.

        Returns:
            An async generator that yields messages from the underlying queue.
        """
        callback, generator = self._callback_and_generator()
        try:
            # Add to the list of callbacks to call when a message is received
            self._notification_callbacks.append(callback)
            # Back to the caller (run whatever is inside the with statement)
            yield generator
        finally:
            # We are exiting the with statement. Remove the callback from the list.
            self._notification_callbacks.remove(callback)

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

    async def avss_request(
        self, addr: models.BluetoothAddrLE, data: bytes
    ) -> models.AVSSRequestResult:
        args = models.AVSSRequestArgs(address=addr, data=data)
        return await self.request(
            "avss_request", args, result_type=models.AVSSRequestResult
        )

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
