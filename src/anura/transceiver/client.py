import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Generator
from contextlib import contextmanager
from typing import (
    Any,
    TypeVar,
    overload,
)

import cbor2

from anura.marshalling import marshal, unmarshal

from . import models
from .exceptions import (
    TransceiverConnectionError,
    TransceiverError,
    TransceiverMethodNotFoundError,
    TransceiverRequestError,
)
from .models import APIErrorCode
from .transport import Transport

T = TypeVar("T")

logger = logging.getLogger(__name__)


class TransceiverClient:
    # Interval between keepalive pings. The transceiver's TCP server closes
    # connections it has received nothing on for 5 seconds.
    _keepalive_interval = 1.0

    # How long a request may go unanswered before the transceiver is
    # considered broken and the connection is closed. The transceiver answers
    # every request promptly, so this only needs to absorb link latency and
    # queueing behind other requests.
    _request_timeout = 5.0

    # The transceiver's default bound on how long an avss_request waits for
    # the node, applied when the request carries no timeout of its own.
    _avss_request_default_timeout = 30.0

    # Margin on top of the node timeout that the transceiver gets to deliver
    # the avss_request response (or its timeout error).
    _avss_request_margin = 5.0

    def __init__(self, target_spec: str, port: int = 7645) -> None:
        self._transport = Transport.create(target_spec, port)
        self._pending_responses = {}
        self._known_methods: dict[str, int] = {}
        self._connection_task: asyncio.Task[None] | None = None
        self._connection_closed = asyncio.Event()
        self._connection_exception: BaseException | None = None
        self._notification_callbacks: list[Callable[[models.Notification], None]] = []
        self._next_request_token: int = 0
        # Whether the transceiver firmware accepts the avss_request timeout
        # argument. Unknown until a request carrying it has been answered.
        self._avss_request_timeout_supported: bool | None = None

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
            # Keeps traffic flowing so the transceiver does not evict us, and
            # doubles as a liveness probe: an unanswered ping times out like
            # any other request and fails the connection.
            while True:
                await asyncio.sleep(self._keepalive_interval)
                await self.ping()

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(recv_task())
                if self._transport.requires_keepalive:
                    tg.create_task(keep_alive())
        except* TransceiverError as eg:
            if len(eg.exceptions) == 1:
                raise eg.exceptions[0] from None
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

        if not task.cancelled() and self._connection_exception is None:
            self._connection_exception = task.exception()

        self._connection_closed.set()

    def _fail_connection(self, exception: TransceiverError) -> None:
        """Tear down the connection because the transceiver misbehaved.

        Pending requests fail with `TransceiverConnectionError` and
        `wait_for_disconnection` returns, so the owner can reconnect.
        """
        if not self._connection_task or self._connection_task.done():
            return
        logger.error("Closing transceiver connection: %s", exception)
        self._connection_exception = exception
        self._connection_task.cancel()

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
    ) -> tuple[Any, Any]:
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
            response_fut: asyncio.Future[tuple[Any, Any]] = (
                asyncio.get_running_loop().create_future()
            )
            self._pending_responses[request_token] = response_fut

            payload = cbor2.dumps(
                [models.msg_type.Request, request_token, resolved_method, param]
            )
            await self._transport.send(payload)

            try:
                async with asyncio.timeout(timeout):
                    async with asyncio.TaskGroup() as tg:
                        monitor_task = tg.create_task(self._connection_closed.wait())
                        done, _ = await asyncio.wait(
                            [monitor_task, response_fut],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        monitor_task.cancel()
                        response_fut.cancel()
            except TimeoutError:
                # The transceiver answers every request promptly, so silence
                # means it is broken. Close the connection so the owner can
                # reconnect.
                self._fail_connection(
                    TransceiverError(f'"{method}" request unanswered for {timeout} s')
                )
                await self.wait_for_disconnection()
                raise TransceiverConnectionError(
                    f'Transceiver did not answer "{method}" request within '
                    f"{timeout} s; connection closed"
                ) from None

            if response_fut in done:
                return response_fut.result()
            elif self._connection_exception:
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
        self,
        method: str,
        arg: Any = None,
        /,
        *,
        result_type: type[T],
        timeout: float | bool | None = True,
    ) -> T: ...

    @overload
    async def request(
        self, method: str, arg: Any = None, /, *, timeout: float | bool | None = True
    ) -> Any: ...

    async def request(
        self, method, arg=None, result_type=None, timeout: float | bool | None = True
    ):
        """Send a request and receive the response.

        Args:
            timeout: Seconds to wait for the response, True for the default
                     timeout, None for no timeout.

        Raises:
            TransceiverConnectionError: If the device does not answer in time.
                The connection is closed, as an unanswered request means the
                transceiver is broken.
        """
        if timeout is True:
            timeout = self._request_timeout
        if timeout is False:
            timeout = None

        # The request runs in its own task so that a caller giving up on it
        # (cancellation, or a timeout imposed by a higher layer such as
        # AVSSClient) does not abort it. The exchange with the transceiver
        # continues to completion or to this client's own timeout, so the
        # response is consumed and the transceiver's health is still judged
        # by the request's outcome rather than by the caller's patience.
        task = asyncio.create_task(
            self._request_internal(method, marshal(arg), timeout=timeout)
        )
        task.add_done_callback(_consume_abandoned_result)

        match await asyncio.shield(task):
            case (None, result):
                if result_type:
                    return unmarshal(result_type, result)
                else:
                    return result
            case (error, _):
                if error == ".well-known/not-found":
                    raise TransceiverMethodNotFoundError(method)
                else:
                    api_error = unmarshal(models.APIError, error)
                    raise TransceiverRequestError(method, api_error)

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

    async def dfu_write_image(
        self,
        image: bytes,
        chunk_size: int = 300,
        progress: Callable[[int], None] | None = None,
    ):
        """Write a firmware image in chunks, optionally reporting progress.

        Args:
            image:      Raw firmware binary (after ``dfu_prepare`` was called).
            chunk_size: Number of bytes per ``dfu_write`` request.
            progress:   Optional callback invoked with the cumulative number of
                        bytes written so far, after each chunk.
        """
        offset = 0
        total = len(image)
        while offset < total:
            end = min(offset + chunk_size, total)
            await self.dfu_write(offset, image[offset:end])
            offset = end
            if progress is not None:
                progress(offset)

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
        # Deliberately slow diagnostic method, answered after 5 seconds.
        return await self.request("slow_ping", timeout=10.0)

    async def scan_nodes_stop(self):
        return await self.request("scan_nodes_stop")

    async def avss_request(
        self,
        addr: models.BluetoothAddrLE,
        data: bytes,
        *,
        timeout: float | None = None,
    ) -> models.AVSSRequestResult:
        """Send a control point request to a node via the transceiver.

        Args:
            timeout: Seconds to wait for the node's response. Passed on to the
                     transceiver, which fails the request with
                     `APIErrorCode.TIMEOUT` and disconnects the node when it
                     expires. Firmware without support for the argument gets
                     the same limit enforced here instead. None leaves the
                     transceiver's default (30 s) in force.

        Raises:
            TimeoutError: If the node does not answer within `timeout`.
            TransceiverRequestError: With `APIErrorCode.TIMEOUT` if the
                transceiver's own limit expired.
            TransceiverConnectionError: If the transceiver fails to deliver
                any answer within its bound plus a margin. As for any
                unanswered request, the connection is closed.
        """
        # Hand the limit to the transceiver unless it is known to reject it, in
        # which case it is enforced locally instead. Either way the transceiver
        # is expected to answer within the node timeout it is applying, plus a
        # margin.
        timeout_ms: int | None = None
        local_timeout: float | None = None
        node_timeout = self._avss_request_default_timeout
        if timeout is not None:
            if self._avss_request_timeout_supported is not False:
                timeout_ms = int(timeout * 1000)
                node_timeout = timeout
            else:
                local_timeout = timeout
        rpc_timeout = node_timeout + self._avss_request_margin
        send_timeout = timeout_ms is not None

        args = models.AVSSRequestArgs(address=addr, data=data, timeout_ms=timeout_ms)

        try:
            # request() shields the exchange, so the local timeout only stops
            # us waiting; the transceiver's answer is still consumed.
            async with asyncio.timeout(local_timeout):
                result = await self.request(
                    "avss_request",
                    args,
                    result_type=models.AVSSRequestResult,
                    timeout=rpc_timeout,
                )
        except TransceiverRequestError as e:
            if (
                send_timeout
                and e.error.code == APIErrorCode.ARGUMENT_DECODE
                and self._avss_request_timeout_supported is None
            ):
                # Older firmware rejects the timeout argument. Remember and
                # retry without it, enforcing the limit here instead.
                logger.warning(
                    "Transceiver firmware does not support the avss_request "
                    "timeout argument; enforcing node timeouts client-side"
                )
                self._avss_request_timeout_supported = False
                return await self.avss_request(addr, data, timeout=timeout)
            if send_timeout:
                # Any other error means the argument itself was accepted.
                self._avss_request_timeout_supported = True
            raise

        if send_timeout:
            self._avss_request_timeout_supported = True
        return result

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


def _consume_abandoned_result(task: asyncio.Task) -> None:
    """Retrieve the outcome of a request task its caller stopped waiting for,
    so the event loop does not log it as never retrieved."""
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        logger.debug("Abandoned request finished with %r", exc)
