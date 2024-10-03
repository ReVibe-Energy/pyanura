from .models import *
from .settings import SettingsMapper

import asyncio
import cbor2
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import logging
import struct
import time
import types
from typing import Optional
from typing import (
    AsyncGenerator,
    Callable,
    Generator,
)

logger = logging.getLogger(__name__)

class AVSSError(Exception):
    pass

class AVSSProtocolError(AVSSError):
    pass

class DisconnectedError(AVSSError):
    pass

class AVSSControlPointError(AVSSError):
    pass

    def from_response_code(rc):
        if rc == ResponseCode.OK:
            raise ValueError("Not an error response code")
        if rc == ResponseCode.Error:
            return AVSSControlPointError("Unspecified error")
        elif rc == ResponseCode.OpCodeUnsupported:
            return AVSSOpCodeUnsupportedError()
        elif rc == ResponseCode.Busy:
            return AVSSBusyError()
        elif rc == ResponseCode.BadArgument:
            return AVSSBadArgumentError()
        else:
            return AVSSControlPointError(f"Response code {rc}")

class AVSSBusyError(AVSSControlPointError):
    pass

class AVSSBadArgumentError(AVSSControlPointError):
    pass

class AVSSOpCodeUnsupportedError(AVSSControlPointError):
    pass

OpCode = types.SimpleNamespace()
OpCode.ResponseCode = 1
OpCode.ReportSnippet = 2
OpCode.ReportAggregates = 3
OpCode.ReportHealth = 4
OpCode.GetVersion = 5
OpCode.GetVersionResponse = 6
OpCode.WriteSettings = 7
OpCode.WriteSettingsResponse = 8
OpCode.ReportSettings = 9
OpCode.ApplySettings = 10
OpCode.ApplySettingsResponse = 11
OpCode.TestThroughput = 12 # TBD
OpCode.ReportCapture = 13 # TBD
OpCode.Deactivate = 16
OpCode.TriggerMeasurement = 17
OpCode.PrepareUpgrade = 100
OpCode.ApplyUpgrade = 101
OpCode.ConfirmUpgrade = 102
OpCode.Reboot = 103

ResponseCode = types.SimpleNamespace()
ResponseCode.OK = 1
ResponseCode.Error = 2
ResponseCode.OpCodeUnsupported = 3
ResponseCode.Busy = 4
ResponseCode.BadArgument = 5

SEGMENT_FIRST = 0x80
SEGMENT_LAST = 0x40
SEGMENT_NUMBER_MASK = 0x3F

@dataclass
class ReportTransferInfo:
    start_time: float
    elapsed_time: float
    num_bytes: int
    num_segments: int

class ReportBuffer:
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
            elapsed_time=time.time()-self.start_time,
            num_bytes=len(self._buffer),
            num_segments=self.num_segments)
        return self._buffer, transfer_info

class AVSSClient:
    def __init__(self):
        self._report_buf = None
        self._on_report_callbacks = []
        self._disconnected = asyncio.Future()
        self._program_lock = asyncio.Lock()
        self._program_nack_queue = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def _callback_and_generator(self) -> tuple[Callable[[Report], None], AsyncGenerator[Report, None]]:
        # Queue to hold the incoming reports
        reports: asyncio.Queue[Report] = asyncio.Queue()

        def _callback(msg: Report) -> None:
            """Put the new Report in the queue."""
            try:
                reports.put_nowait(msg)
            except asyncio.QueueFull:
                logger.warning("Report queue is full. Discarding message.")

        async def _generator() -> AsyncGenerator[Report, None]:
            """Forward all Reports from the report queue."""
            while True:
                # Wait until we either:
                #  1. Receive a Report
                #  2. Disconnect from the node
                loop = asyncio.get_running_loop()
                get: asyncio.Task[Report] = loop.create_task(reports.get())
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
                    # We received a Report. Return the result.
                    yield get.result()
                else:
                    # We got disconnected. Cancel the "get" task.
                    get.cancel()
                    # Stop the generator with the following exception
                    raise DisconnectedError("Disconnected during Report iteration")

        return _callback, _generator()

    @contextmanager
    def reports(self) -> Generator[AsyncGenerator[Report, None], None, None]:
        """Context manager that creates a queue for incoming Reports.

        Returns:
            An async generator that yields reports from the underlying queue.
        """
        callback, generator = self._callback_and_generator()
        try:
            # Add to the list of callbacks to call when a message is received
            self._on_report_callbacks.append(callback)
            # Back to the caller (run whatever is inside the with statement)
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
            self._report_buf = ReportBuffer()
            self._report_next_segment_number = segment_number

        if self._report_buf is None:
            # Waiting for a SEGMENT_FIRST to synchronize with the stream.
            return

        if self._report_next_segment_number == segment_number:
            self._report_buf.append_segment(segment_payload)
            self._report_next_segment_number = (
                (self._report_next_segment_number + 1) &SEGMENT_NUMBER_MASK)
        else:
            logger.warning("Expected segment %d but got %d",
                self._report_next_segment_number, segment_number)
            self._report_buf = None
            return

        if segment_hdr & SEGMENT_LAST:
            buffer, transfer_info = self._report_buf.finish()
            report = Report.parse(buffer)
            report.transfer_info = transfer_info
            for callback in self._on_report_callbacks:
                try:
                    callback(report)
                except Exception:
                    logger.error("Handling report failed", exc_info=True)
            self._report_buf = None

    async def _request_raw(self, req, timeout):
        raise NotImplementedError()

    async def _request(self, opcode, argument, timeout=2.0):
        req = bytearray([opcode])
        if isinstance(argument, dict):
            req.extend(cbor2.dumps(argument))
        elif argument:
            req.extend(argument.to_cbor())
        else:
            req.extend(cbor2.dumps(None))
        try:
            logger.debug("Sending Control Point request")
            chrc_value = await self._request_raw(req, timeout)
            logger.debug("Control Point request completed")
        except Exception:
            logger.error("Control Point request aborted")
            raise
        response_opcode = chrc_value[0]

        if response_opcode == OpCode.ResponseCode:
            request_opcode = chrc_value[1]
            response_code = chrc_value[2]
            if request_opcode != opcode:
                logger.warning("Request opcode mismatch received: %d expected: %d",
                    request_opcode, opcode)
            if response_code != ResponseCode.OK:
                raise AVSSControlPointError.from_response_code(response_code)
            return None
        elif response_opcode == OpCode.GetVersionResponse:
            return GetVersionResponse.from_cbor(chrc_value[1:])
        elif response_opcode == OpCode.WriteSettingsResponse:
            return WriteSettingsResponse.from_cbor(chrc_value[1:])
        elif response_opcode == OpCode.ApplySettingsResponse:
            return ApplySettingsResponse.from_cbor(chrc_value[1:])
        else:
            raise AVSSProtocolError("Expected response opcode")

    async def _program_write(self, req):
        raise NotImplementedError()

    async def report_snippets(self, count, auto_resume):
        arg = ReportSnippetArgs(count=count, auto_resume=auto_resume)
        return await self._request(OpCode.ReportSnippet, arg)

    async def report_capture(self, count):
        arg = ReportCaptureArgs(count=count)
        return await self._request(OpCode.ReportCapture, arg)

    async def report_aggregates(self, count, auto_resume):
        arg = ReportAggregatesArgs(count=count, auto_resume=auto_resume)
        return await self._request(OpCode.ReportAggregates, arg)

    async def report_health(self, active):
        arg = ReportHealthArgs(active=active)
        return await self._request(OpCode.ReportHealth, arg)

    async def report_settings(self):
        arg = ReportSettings()
        return await self._request(OpCode.ReportSettings, arg)

    async def apply_settings(self, persist: bool) -> ApplySettingsResponse:
        arg = ApplySettingsArgs(persist=persist)
        return await self._request(OpCode.ApplySettings, arg)

    async def prepare_upgrade(self, image, size, timeout=30.0):
        arg = PrepareUpgradeArgs(image=image, size=size)
        return await self._request(OpCode.PrepareUpgrade, arg, timeout=timeout)

    async def apply_upgrade(self):
        arg = ApplyUpgradeArgs()
        return await self._request(OpCode.ApplyUpgrade, arg)

    async def confirm_upgrade(self, image):
        arg = ConfirmUpgradeArgs(image=image)
        return await self._request(OpCode.ConfirmUpgrade, arg)

    async def reboot(self):
        return await self._request(OpCode.Reboot, None)

    async def get_version(self) -> GetVersionResponse:
        return await self._request(OpCode.GetVersion, None)

    async def write_settings(self, settings: dict) -> WriteSettingsResponse:
        return await self._request(OpCode.WriteSettings, SettingsMapper.from_readable(settings))

    async def test_throughput(self, duration: int):
        args = TestThroughputArgs(duration=duration)
        return await self._request(OpCode.TestThroughput, args)

    async def deactivate(self, key: int):
        arg = DeactivateArgs(key=key)
        return await self._request(OpCode.Deactivate, arg)

    async def trigger_measurement(self, duration_ms: int):
        arg = TriggerMeasurementArgs(duration_ms=duration_ms)
        return await self._request(OpCode.TriggerMeasurement, arg)

    def _on_program_notify(self, data):
        offset, = struct.unpack("<L", data)
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
                        offset = await asyncio.wait_for(self._program_nack_queue.get(), timeout=0.01)
                        if offset == 0xFFFFFFFF:
                            raise RuntimeError("Program transfer aborted")
                        # We received a NACK so we wait a short while to see
                        # if any more NACKs turn up before we continue writing.
                        # This aids re-synchronization if multiple write requests
                        # are queued .
                        await asyncio.sleep(0.1)
                except TimeoutError:
                    # No NACK was received after 10 ms of waiting so we assume
                    # the write operation is on track.
                    pass
                end = offset + chunk_size
                req = bytearray(struct.pack("<L", offset))
                if end < len(binary):
                    req.extend(binary[offset:end])
                else:
                    req.extend(binary[offset:])
                offset = end
                logger.info(f"Program {offset}/{len(binary)} ({offset * 100 / len(binary):.0f} %)")
                await self._program_write(req)
