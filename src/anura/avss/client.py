import asyncio
import logging
import struct
import time
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Iterator,
    Literal,
    Optional,
    TypeAlias,
    TypeVar,
    overload,
)

import cbor2

from anura.marshalling import marshal, unmarshal

from .exceptions import (
    AVSSConnectionError,
    AVSSControlPointError,
    AVSSProtocolError,
    AVSSTransportError,
)
from .models import (
    AggregatedValuesReport,
    ApplySettingsArgs,
    ApplySettingsResponse,
    ApplyUpgradeArgs,
    CaptureReport,
    ConfirmUpgradeArgs,
    DeactivateArgs,
    GetFirmwareInfoResponse,
    GetVersionResponse,
    HealthReport,
    PrepareUpgradeArgs,
    ReportAggregatesArgs,
    ReportCaptureArgs,
    ReportHealthArgs,
    ReportSettings,
    ReportSnippetArgs,
    SettingsReport,
    SnippetReport,
    TestThroughputArgs,
    TriggerCaptureArgs,
    TriggerMeasurementArgs,
    WriteSettingsResponse,
    WriteSettingsV2Args,
    WriteSettingsV2Response,
)
from .protocol import OpCode, ReportType, ResponseCode
from .settings import SettingsMapper
from .transport.base import AVSSTransport

_TResp = TypeVar("_TResp")

logger = logging.getLogger(__name__)

_ParsedReport: TypeAlias = (
    AggregatedValuesReport
    | CaptureReport
    | HealthReport
    | SettingsReport
    | SnippetReport
)


SEGMENT_FIRST = 0x80
SEGMENT_LAST = 0x40
SEGMENT_NUMBER_MASK = 0x3F


@dataclass
class ReportTransferInfo:
    start_time: float
    elapsed_time: float
    num_bytes: int
    num_segments: int


class Report:
    report_type: int
    payload_cbor: bytes
    _transfer_info: Optional[ReportTransferInfo]

    def __init__(
        self,
        report_type: int,
        payload_cbor: bytes,
        transfer_info: Optional[ReportTransferInfo] = None,
    ):
        self.report_type = report_type
        self.payload_cbor = payload_cbor
        self._transfer_info = transfer_info

    @staticmethod
    def from_record(
        record: bytes, transfer_info: Optional[ReportTransferInfo] = None
    ) -> "Report":
        return Report(
            report_type=record[0], payload_cbor=record[1:], transfer_info=transfer_info
        )

    def parse(self) -> _ParsedReport | None:
        report_classes: dict[int, type] = {
            int(ReportType.SNIPPET): SnippetReport,
            int(ReportType.AGGREGATES): AggregatedValuesReport,
            int(ReportType.HEALTH): HealthReport,
            int(ReportType.SETTINGS): SettingsReport,
            int(ReportType.CAPTURE): CaptureReport,
        }
        if report_class := report_classes.get(self.report_type):
            return unmarshal(report_class, cbor2.loads(self.payload_cbor))
        else:
            return None


class _ReportBuffer:
    def __init__(self):
        self.start_time: float = time.time()
        self.end_time: Optional[float] = None
        self.num_segments: int = 0
        self._buffer = bytearray()
        self._finished = False

    def append_segment(self, segment):
        if self._finished:
            raise RuntimeError("Cannot append segment to finished buffer")
        self._buffer.extend(segment)
        self.num_segments += 1

    def finish(self):
        if self._finished:
            raise RuntimeError("Buffer is already finished")
        self._finished = True
        transfer_info = ReportTransferInfo(
            start_time=self.start_time,
            elapsed_time=time.time() - self.start_time,
            num_bytes=len(self._buffer),
            num_segments=self.num_segments,
        )
        return Report.from_record(bytes(self._buffer), transfer_info=transfer_info)


class AVSSClient:
    def __init__(self, transport: AVSSTransport):
        """Initialize AVSSClient with a transport.

        Args:
            transport: An AVSSTransport instance to use for communication.
                       The transport should be opened by the caller before use.
        """
        self._transport = transport
        self._transport_closed = asyncio.Event()
        self._report_buf = None
        self._on_report_callbacks = []
        self._program_lock = asyncio.Lock()
        self._program_nack_queue = None
        self._control_point_lock = asyncio.Lock()

        # Register callbacks with transport
        transport.set_report_callback(self._on_report_notify)
        transport.set_program_callback(self._on_program_notify)
        transport.set_closed_callback(self._transport_closed.set)

    async def wait_for_disconnection(self) -> None:
        await self._transport_closed.wait()

    def _callback_and_generator(
        self,
    ) -> tuple[Callable[[Report], None], AsyncIterator[Report]]:

        queue: asyncio.Queue[Report] = asyncio.Queue()

        def _callback(report: Report) -> None:
            queue.put_nowait(report)

        async def _generator() -> AsyncIterator[Report]:
            async with asyncio.TaskGroup() as tg:
                monitor_task = tg.create_task(self._transport_closed.wait())

                while True:
                    get_task = tg.create_task(queue.get())

                    done, _ = await asyncio.wait(
                        (monitor_task, get_task), return_when=asyncio.FIRST_COMPLETED
                    )

                    if get_task in done:
                        yield get_task.result()
                    else:
                        get_task.cancel()
                        break

            raise AVSSConnectionError("Disconnected during report iteration.")

        return _callback, _generator()

    @overload
    @contextmanager
    def reports(self, parse: Literal[False]) -> Iterator[AsyncIterator[Report]]: ...

    @overload
    @contextmanager
    def reports(
        self, parse: Literal[True] = True
    ) -> Iterator[AsyncIterator[_ParsedReport]]: ...

    @contextmanager
    def reports(
        self, parse: bool = True
    ) -> Iterator[AsyncIterator[_ParsedReport]] | Iterator[AsyncIterator[Report]]:
        """Context manager that creates a queue for incoming Reports.

        Returns:
            An async generator that yields reports from the underlying queue.
        """
        callback, generator = self._callback_and_generator()
        try:
            # Add to the list of callbacks to call when a message is received
            self._on_report_callbacks.append(callback)

            # Back to the caller (run whatever is inside the with statement)
            if parse:
                parsed_report_generator = (
                    parsed async for report in generator if (parsed := report.parse())
                )
                yield parsed_report_generator
            else:
                yield generator
        finally:
            # We are exiting the with statement. Remove the callback from the list.
            self._on_report_callbacks.remove(callback)

    def _on_report_notify(self, segment):
        """Handle Report characteristic notifications"""

        segment_hdr = segment[0]
        segment_number = segment_hdr & SEGMENT_NUMBER_MASK
        segment_payload = segment[1:]

        logger.debug("Report segment received")

        if segment_hdr & SEGMENT_FIRST:
            if self._report_buf is not None:
                logger.warning("Report aborted")
            self._report_buf = _ReportBuffer()
            self._report_next_segment_number = segment_number

        if self._report_buf is None:
            # Waiting for a SEGMENT_FIRST to synchronize with the stream.
            return

        if self._report_next_segment_number == segment_number:
            self._report_buf.append_segment(segment_payload)
            self._report_next_segment_number = (
                self._report_next_segment_number + 1
            ) & SEGMENT_NUMBER_MASK
        else:
            logger.warning(
                "Expected segment %d but got %d",
                self._report_next_segment_number,
                segment_number,
            )
            self._report_buf = None
            return

        if segment_hdr & SEGMENT_LAST:
            report = self._report_buf.finish()
            for callback in self._on_report_callbacks:
                try:
                    callback(report)
                except Exception:
                    logger.error("Handling report failed", exc_info=True)
            self._report_buf = None

    async def _request(
        self, opcode: OpCode, argument: Any, *, timeout: float | bool | None = True
    ) -> tuple[OpCode, bytes]:
        """Send request and return raw response.

        This is the primary internal request method for Control Point requests.
        It handles serialization, error responses (OpCode.RESPONSE), and returns
        the raw response for the caller to parse based on expected response type(s).

        Args:
            opcode: Request opcode to send
            argument: Request argument (will be marshaled to CBOR)
            timeout: Timeout in seconds, True for default, None for no timeout

        Returns:
            Tuple of (response_opcode, response_payload) where:
            - response_opcode: The opcode from the response
            - response_payload: Raw bytes after the opcode byte (ready for unmarshaling)

        Raises:
            TimeoutError: If the request times out
            AVSSControlPointError: If response code is not OK
            AVSSProtocolError: If response is malformed or opcode mismatch occurs
        """
        if timeout is True:
            timeout = 5.0  # TODO: self._control_point_timeout

        if not timeout:
            timeout = None

        # Serialize request
        with BytesIO() as fp:
            fp.write(bytes((opcode,)))
            cbor2.dump(marshal(argument), fp)
            req_bytes = fp.getvalue()

        # Send request and await response
        async with asyncio.timeout(timeout):
            try:
                async with self._control_point_lock:
                    resp_bytes = await self._transport.control_point_request(req_bytes)
            except AVSSConnectionError:
                raise
            except Exception as e:
                raise AVSSTransportError(f"Request failed: {str(e)}") from e

        # Get response opcode
        try:
            resp_opcode = OpCode(resp_bytes[0])
        except IndexError:
            raise AVSSProtocolError("Received empty response") from None
        except ValueError:
            raise AVSSProtocolError(
                f"Unknown response opcode: received {resp_bytes[0]}, "
                f"expected response for {opcode.name}"
            ) from None

        # Handle RESPONSE opcode mismatch and error response codes
        if resp_opcode == OpCode.RESPONSE:
            if len(resp_bytes) != 3:
                raise AVSSProtocolError(
                    f"Malformed payload for {OpCode.RESPONSE.name} opcode."
                )
            resp_request_opcode = resp_bytes[1]
            resp_response_code = resp_bytes[2]

            # Match request opcode in the response to the opcode we sent.
            if resp_request_opcode != opcode:
                raise AVSSProtocolError(
                    f"Response opcode mismatch: received response for "
                    f"{OpCode._safe_name(resp_request_opcode)}, "
                    f"expected response for {opcode.name}"
                )

            # Raise exception if response code is not OK
            if resp_response_code != ResponseCode.OK:
                raise AVSSControlPointError.from_response(
                    resp_response_code, opcode=opcode
                )

        return resp_opcode, resp_bytes[1:]

    async def _void_request(
        self, opcode: OpCode, argument, *, timeout: float | bool | None = True
    ) -> None:
        resp_opcode, _ = await self._request(opcode, argument, timeout=timeout)

        if resp_opcode != OpCode.RESPONSE:
            raise AVSSProtocolError.unexpected_response(
                opcode, resp_opcode, expected=OpCode.RESPONSE
            )

        return None

    async def report_snippets(self, count, auto_resume):
        arg = ReportSnippetArgs(count=count, auto_resume=auto_resume)
        return await self._void_request(OpCode.REPORT_SNIPPETS, arg)

    async def report_capture(self, count, auto_resume):
        arg = ReportCaptureArgs(count=count, auto_resume=auto_resume)
        return await self._void_request(OpCode.REPORT_CAPTURE, arg)

    async def report_aggregates(self, count, auto_resume):
        arg = ReportAggregatesArgs(count=count, auto_resume=auto_resume)
        return await self._void_request(OpCode.REPORT_AGGREGATES, arg)

    async def report_health(
        self, count: int | None = None, *, active: bool | None = None
    ):
        if active is not None:
            arg = ReportHealthArgs(count=active)
        elif count is None:
            # Send True instead of None since this is compatible
            # with older sensor firmware versions.
            arg = ReportHealthArgs(count=True)
        else:
            arg = ReportHealthArgs(count=count)
        return await self._void_request(OpCode.REPORT_HEALTH, arg)

    async def report_settings(self, current=True, pending=False):
        arg = ReportSettings(current=current, pending=pending)
        return await self._void_request(OpCode.REPORT_SETTINGS, arg)

    async def apply_settings(self, persist: bool) -> ApplySettingsResponse | None:
        """Apply settings to node.

        Returns ApplySettingsResponse for newer firmware (v24.6.0+), None for older
        firmware that only sends generic OK response.
        """
        arg = ApplySettingsArgs(persist=persist)
        resp_opcode, resp_payload = await self._request(OpCode.APPLY_SETTINGS, arg)
        match resp_opcode:
            case OpCode.RESPONSE:
                # Older firmware (< v24.6.0) - generic OK response
                return None
            case OpCode.APPLY_SETTINGS_RESPONSE:
                # Newer firmware (v24.6.0+) - detailed response
                return unmarshal(ApplySettingsResponse, cbor2.loads(resp_payload))
            case _:
                raise AVSSProtocolError.unexpected_response(
                    OpCode.APPLY_SETTINGS,
                    resp_opcode,
                    expected={OpCode.RESPONSE, OpCode.APPLY_SETTINGS_RESPONSE},
                )

    async def prepare_upgrade(self, image, size, timeout=30.0):
        arg = PrepareUpgradeArgs(image=image, size=size)
        return await self._void_request(OpCode.PREPARE_UPGRADE, arg, timeout=timeout)

    async def apply_upgrade(self):
        arg = ApplyUpgradeArgs()
        return await self._void_request(OpCode.APPLY_UPGRADE, arg)

    async def confirm_upgrade(self, image):
        arg = ConfirmUpgradeArgs(image=image)
        return await self._void_request(OpCode.CONFIRM_UPGRADE, arg)

    async def reboot(self):
        return await self._void_request(OpCode.REBOOT, None)

    async def get_version(self) -> GetVersionResponse:
        resp_opcode, resp_payload = await self._request(OpCode.GET_VERSION, None)
        if resp_opcode != OpCode.GET_VERSION_RESPONSE:
            raise AVSSProtocolError.unexpected_response(
                OpCode.GET_VERSION, resp_opcode, expected=OpCode.GET_VERSION_RESPONSE
            )
        return unmarshal(GetVersionResponse, cbor2.loads(resp_payload))

    async def write_settings(self, settings: dict) -> WriteSettingsResponse | None:
        """Write settings to node.

        Returns WriteSettingsResponse for newer firmware (v24.4.1+), None for older
        firmware that only sends generic OK response.
        """
        arg = SettingsMapper.from_readable(settings)
        resp_opcode, resp_param = await self._request(OpCode.WRITE_SETTINGS, arg)
        match resp_opcode:
            case OpCode.RESPONSE:
                # Older firmware (< v24.4.1) - generic OK response
                return None
            case OpCode.WRITE_SETTINGS_RESPONSE:
                # Newer firmware (v24.4.1+) - detailed response
                return unmarshal(WriteSettingsResponse, cbor2.loads(resp_param))
            case _:
                raise AVSSProtocolError.unexpected_response(
                    OpCode.WRITE_SETTINGS,
                    resp_opcode,
                    expected={OpCode.RESPONSE, OpCode.WRITE_SETTINGS_RESPONSE},
                )

    async def reset_settings(self):
        return await self._void_request(OpCode.RESET_SETTINGS, None)

    async def test_throughput(self, duration: int):
        args = TestThroughputArgs(duration=duration)
        return await self._void_request(OpCode.TEST_THROUGHPUT, args)

    async def deactivate(self, key: int):
        arg = DeactivateArgs(key=key)
        return await self._void_request(OpCode.DEACTIVATE, arg)

    async def get_firmware_info(self) -> GetFirmwareInfoResponse:
        resp_opcode, resp_payload = await self._request(OpCode.GET_FIRMWARE_INFO, None)
        if resp_opcode != OpCode.GET_FIRMWARE_INFO_RESPONSE:
            raise AVSSProtocolError.unexpected_response(
                OpCode.GET_FIRMWARE_INFO,
                resp_opcode,
                expected=OpCode.GET_FIRMWARE_INFO_RESPONSE,
            )
        return unmarshal(GetFirmwareInfoResponse, cbor2.loads(resp_payload))

    async def reset_report(self):
        return await self._void_request(OpCode.RESET_REPORT, None)

    async def write_settings_v2(
        self, settings: dict[int, Any], reset_defaults: bool, apply: bool
    ) -> WriteSettingsV2Response:
        arg = WriteSettingsV2Args(
            settings=SettingsMapper.from_readable(settings),
            reset_defaults=reset_defaults,
            apply=apply,
        )
        resp_opcode, resp_payload = await self._request(OpCode.WRITE_SETTINGS_V2, arg)
        if resp_opcode != OpCode.WRITE_SETTINGS_V2_RESPONSE:
            raise AVSSProtocolError.unexpected_response(
                OpCode.WRITE_SETTINGS_V2,
                resp_opcode,
                expected=OpCode.WRITE_SETTINGS_V2_RESPONSE,
            )
        return unmarshal(WriteSettingsV2Response, cbor2.loads(resp_payload))

    async def trigger_measurement(self, duration_ms: int):
        arg = TriggerMeasurementArgs(duration_ms=duration_ms)
        return await self._void_request(OpCode.TRIGGER_MEASUREMENT, arg)

    async def trigger_capture(self, duration_ms: int):
        arg = TriggerCaptureArgs(duration_ms=duration_ms)
        return await self._void_request(OpCode.TRIGGER_CAPTURE, arg)

    def _on_program_notify(self, data):
        (offset,) = struct.unpack("<L", data)
        if self._program_nack_queue:
            self._program_nack_queue.put_nowait(offset)

    async def program_transfer(self, binary, att_mtu=243):
        # Write without response is limited to ATT MTU - 3 and
        # we use 4 bytes for offset.
        chunk_size = (att_mtu - 3) - 4
        offset = 0

        async with self._program_lock:
            self._program_nack_queue = asyncio.Queue()

            while offset < len(binary):
                try:
                    while True:
                        # Wait a short while for a NACK message to indicate the
                        # node is not in sync with our writes.
                        offset = await asyncio.wait_for(
                            self._program_nack_queue.get(), timeout=0.04
                        )
                        if offset == 0xFFFFFFFF:
                            raise RuntimeError("Program transfer aborted")
                        # We received a NACK so we wait a short while to see
                        # if any more NACKs turn up before we continue writing.
                        # This aids re-synchronization if multiple write requests
                        # are queued .
                        await asyncio.sleep(0.1)
                except TimeoutError:
                    # No NACK was received after 40 ms of waiting so we assume
                    # the write operation is on track.
                    pass
                end = offset + chunk_size
                req = bytearray(struct.pack("<L", offset))
                if end < len(binary):
                    req.extend(binary[offset:end])
                else:
                    req.extend(binary[offset:])
                offset = end
                logger.info(
                    f"Program {offset}/{len(binary)} ({offset * 100 / len(binary):.0f} %)"
                )
                await self._transport.program_write(bytes(req))
