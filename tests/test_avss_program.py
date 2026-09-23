"""Tests for the Program characteristic firmware transfer loops."""

import asyncio
import hashlib
import struct
import time
from collections.abc import Callable
from itertools import pairwise

import cbor2
import pytest

from anura.avss import procedures
from anura.avss.client import PROGRAM_OFFSET_ABORT, AVSSClient
from anura.avss.exceptions import AVSSConnectionError, AVSSProgramTransferError
from anura.avss.models import (
    PrepareUpgradeArgs,
    PrepareUpgradeV2Args,
    PrepareUpgradeV2Response,
)
from anura.avss.protocol import OpCode, ResponseCode
from anura.avss.transport.base import AVSSTransport
from anura.marshalling import marshal, unmarshal


class FakeSensorTransport(AVSSTransport):
    """Emulates the sensor side of the Program characteristic.

    Mirrors the firmware behaviour of avss_dfu.c: in-order chunk acceptance,
    a chunk-credit receive window, periodic ack notifications, rewind
    requests repeating the last acked offset, single recovery acks for
    duplicate bursts, repetition of the final ack for writes into a
    completed transfer, and resumption of a matching interrupted transfer.
    """

    def __init__(
        self,
        image_size,
        *,
        max_chunk=252,
        window_chunks=7,
        ack_interval=3,
        windowed_supported=True,
        strict_window=True,
    ):
        self.image_size = image_size
        self.max_chunk = max_chunk
        self.window_chunks = window_chunks
        self.ack_interval = ack_interval
        self.windowed_supported = windowed_supported
        # Enforce the client's outstanding-chunk discipline. Only valid in
        # fault-free runs: after injected drops the bookkeeping of this
        # emulation double-counts rewound writes.
        self.strict_window = strict_window

        self.received = bytearray(image_size)
        self.expected = 0
        self.digest = None
        self.prepared_image = None
        self.last_acked = 0
        self.unacked_chunks = 0
        self.resync = False
        self.dup_acked = False
        self.ready = False
        self.aborted = False
        self.windowed = False
        self.resumed_from: int | None = None

        # Fault injection
        self.drop_chunks: set[int] = set()  # write numbers to drop (overrun)
        self.drop_acks: set[int] = set()  # ack numbers to drop (lost notify)
        # Write numbers the transport fails with TimeoutError instead of
        # delivering, like a transceiver that could not send them in time.
        self.timeout_writes: set[int] = set()
        self.abort_at_write: int | None = None
        # Legacy node that rejects every write with a NACK for its expected
        # offset, never accepting data.
        self.legacy_nack_all = False

        # Bookkeeping for assertions
        self.write_count = 0
        self.ack_count = 0
        self.delivered_acked = 0
        self.pending_ends: list[int] = []
        self.payload_sizes: list[int] = []

        self._program_cb: Callable[[bytes], None] | None = None
        self._closed_cb: Callable[[], None] | None = None

    async def open(self):
        pass

    async def close(self):
        pass

    def set_report_callback(self, callback):
        pass

    def set_program_callback(self, callback):
        self._program_cb = callback

    def set_closed_callback(self, callback):
        self._closed_cb = callback

    def disconnect(self):
        assert self._closed_cb is not None
        self._closed_cb()

    def _notify(self, acked, window):
        self.ack_count += 1
        if self.ack_count not in self.drop_acks:
            self.delivered_acked = max(self.delivered_acked, acked)
            assert self._program_cb is not None
            self._program_cb(struct.pack("<LB", acked, window))

    def _send_ack(self):
        # last_acked is updated even when the notification is dropped,
        # like the firmware which ignores notify errors.
        self.last_acked = self.expected
        self.unacked_chunks = 0
        self._notify(self.expected, self.window_chunks)

    def _fresh_prepare(self, image, digest):
        self.prepared_image = image
        self.digest = digest
        self.received = bytearray(self.image_size)
        self.expected = 0
        self.last_acked = 0
        self.unacked_chunks = 0
        self.resync = False
        self.dup_acked = False
        self.ready = False
        self.aborted = False
        self.delivered_acked = 0
        self.pending_ends = []

    async def control_point_request(self, req, *, timeout=None):
        opcode = req[0]
        payload = cbor2.loads(req[1:])
        if opcode == OpCode.PREPARE_UPGRADE_V2:
            if not self.windowed_supported:
                return bytes([OpCode.RESPONSE, opcode, ResponseCode.OPCODE_UNSUPPORTED])
            args = unmarshal(PrepareUpgradeV2Args, payload)
            assert args.size == self.image_size
            assert len(args.digest) == 32
            resume = (
                self.digest == args.digest
                and self.prepared_image == args.image
                and not self.aborted
            )
            if resume:
                self.resumed_from = self.expected
                self.last_acked = self.expected
                self.unacked_chunks = 0
                self.resync = False
                self.dup_acked = False
                self.delivered_acked = 0
                self.pending_ends = []
            else:
                self.resumed_from = None
                self._fresh_prepare(args.image, args.digest)
            self.windowed = True
            self._send_ack()  # initial window grant
            response = PrepareUpgradeV2Response(
                max_chunk_size=self.max_chunk,
                window=self.window_chunks,
                offset=self.expected,
            )
            return bytes([OpCode.PREPARE_UPGRADE_V2_RESPONSE]) + cbor2.dumps(
                marshal(response)
            )
        if opcode == OpCode.PREPARE_UPGRADE:
            args = unmarshal(PrepareUpgradeArgs, payload)
            assert args.size == self.image_size
            self._fresh_prepare(args.image, None)
            self.windowed = False
            return bytes([OpCode.RESPONSE, opcode, ResponseCode.OK])
        raise AssertionError(f"unexpected opcode {opcode}")

    async def program_write(self, value):
        # A real transport awaits its I/O here, which yields to the loop.
        await asyncio.sleep(0)
        self.write_count += 1
        if self.write_count in self.timeout_writes:
            raise TimeoutError("transport could not send the write in time")
        (offset,) = struct.unpack("<L", value[:4])
        payload = value[4:]
        self.payload_sizes.append(len(payload))
        assert len(payload) <= self.max_chunk

        if self.aborted:
            return

        if self.abort_at_write == self.write_count:
            self.aborted = True
            assert self._program_cb is not None
            self._program_cb(struct.pack("<LB", PROGRAM_OFFSET_ABORT, 0))
            return

        if self.ready:
            # Retransmission into a completed transfer: repeat the final ack.
            if self.windowed and not self.dup_acked:
                self.dup_acked = True
                self._notify(self.image_size, self.window_chunks)
            return

        if self.windowed and self.strict_window:
            self.pending_ends = [
                e for e in self.pending_ends if e > self.delivered_acked
            ]
            self.pending_ends.append(offset + len(payload))
            assert len(self.pending_ends) <= self.window_chunks

        if self.write_count in self.drop_chunks:
            return

        if not self.windowed:
            # Legacy: NACK the expected offset on mismatch.
            if offset != self.expected or self.legacy_nack_all:
                assert self._program_cb is not None
                self._program_cb(struct.pack("<L", self.expected))
                return
        else:
            if offset < self.expected:
                if not self.resync and not self.dup_acked:
                    self.dup_acked = True
                    self._send_ack()
                return
            if offset > self.expected:
                if not self.resync:
                    self.resync = True
                    self._notify(self.last_acked, self.window_chunks)
                return

        self.resync = False
        self.dup_acked = False
        self.received[offset : offset + len(payload)] = payload
        self.expected += len(payload)
        self.unacked_chunks += 1
        if self.expected == self.image_size:
            self.ready = True
        if self.windowed and (self.ready or self.unacked_chunks >= self.ack_interval):
            self._send_ack()


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=30))


def make_binary(size):
    return bytes(i & 0xFF for i in range(size))


ATT_MTU = 243
CHUNK = (ATT_MTU - 3) - 4


def test_windowed_transfer_completes():
    binary = make_binary(10 * CHUNK + 17)
    transport = FakeSensorTransport(len(binary))
    client = AVSSClient(transport)
    progress = []

    run(procedures.upload_firmware(client, binary, image=0, progress=progress.append))

    assert transport.ready
    assert bytes(transport.received) == binary
    assert transport.prepared_image == 0
    assert transport.digest == hashlib.sha256(binary).digest()
    assert progress[-1] == len(binary)
    assert progress == sorted(progress)
    # No retransmissions needed on a clean transfer.
    assert transport.write_count == 11


def test_windowed_transfer_respects_max_chunk():
    binary = make_binary(1000)
    transport = FakeSensorTransport(len(binary), max_chunk=100)
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary
    assert max(transport.payload_sizes) <= 100


def test_windowed_transfer_single_chunk():
    binary = make_binary(10)
    transport = FakeSensorTransport(len(binary))
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary


def test_windowed_transfer_recovers_from_dropped_chunk():
    binary = make_binary(20 * CHUNK)
    transport = FakeSensorTransport(len(binary), strict_window=False)
    transport.drop_chunks = {5}
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary
    assert transport.write_count > 20


def test_windowed_transfer_recovers_from_lost_ack(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_STALL_TIMEOUT", 0.05)
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary), strict_window=False)
    # Ack 1 is the initial window grant; drop a mid-transfer ack.
    transport.drop_acks = {3}
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary


def test_windowed_transfer_recovers_from_lost_final_ack(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_STALL_TIMEOUT", 0.05)
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary), strict_window=False)
    # With an ack interval of 3 chunks: initial grant, acks after chunks
    # 3, 6 and 9, then the final ack after chunk 10 is ack number 5.
    transport.drop_acks = {5}
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary


def test_windowed_transfer_aborted_by_node():
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary))
    transport.abort_at_write = 4
    client = AVSSClient(transport)

    with pytest.raises(AVSSProgramTransferError):
        run(procedures.upload_firmware(client, binary, image=0))


def test_windowed_transfer_fails_on_persistent_stall(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_STALL_TIMEOUT", 0.01)
    monkeypatch.setattr("anura.avss.client.PROGRAM_PROGRESS_TIMEOUT", 0.1)
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary), strict_window=False)
    # Drop every notification after the initial grant.
    transport.drop_acks = set(range(2, 10_000))
    client = AVSSClient(transport)

    with pytest.raises(AVSSProgramTransferError):
        run(procedures.upload_firmware(client, binary, image=0))


def test_windowed_transfer_resumes_after_interruption(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_STALL_TIMEOUT", 0.01)
    monkeypatch.setattr("anura.avss.client.PROGRAM_PROGRESS_TIMEOUT", 0.1)
    binary = make_binary(40 * CHUNK)
    transport = FakeSensorTransport(len(binary), strict_window=False)
    client = AVSSClient(transport)

    # First attempt is starved of feedback and fails with partial progress.
    transport.drop_acks = set(range(2, 10_000))
    with pytest.raises(AVSSProgramTransferError):
        run(procedures.upload_firmware(client, binary, image=0))
    committed = transport.expected
    assert 0 < committed < len(binary)

    # Second attempt resumes from the committed offset.
    transport.drop_acks = set()
    progress = []
    run(procedures.upload_firmware(client, binary, image=0, progress=progress.append))

    assert transport.resumed_from == committed
    assert transport.ready
    assert bytes(transport.received) == binary
    # The resumed transfer starts progress reporting at the resume offset.
    assert progress[0] == committed


def test_windowed_transfer_resume_of_complete_transfer():
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary))
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))
    writes = transport.write_count

    progress = []
    run(procedures.upload_firmware(client, binary, image=0, progress=progress.append))

    assert transport.resumed_from == len(binary)
    assert transport.write_count == writes  # nothing retransmitted
    assert progress == [len(binary)]


def test_windowed_transfer_different_image_restarts():
    binary_a = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary_a))
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary_a, image=0))

    binary_b = bytes(reversed(binary_a))
    run(procedures.upload_firmware(client, binary_b, image=0))

    assert transport.resumed_from is None  # fresh prepare, not a resume
    assert transport.ready
    assert bytes(transport.received) == binary_b


def test_legacy_fallback_transfer_completes(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_SETTLE_TIMEOUT", 0.05)
    binary = make_binary(5 * CHUNK + 3)
    transport = FakeSensorTransport(len(binary), windowed_supported=False)
    client = AVSSClient(transport)
    progress = []

    run(procedures.upload_firmware(client, binary, image=0, progress=progress.append))

    assert transport.ready
    assert not transport.windowed
    assert bytes(transport.received) == binary
    assert progress[-1] == len(binary)


def test_legacy_transfer_recovers_from_late_nack(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_SETTLE_TIMEOUT", 0.05)
    binary = make_binary(6 * CHUNK)
    transport = FakeSensorTransport(len(binary), windowed_supported=False)
    # Drop the second-to-last chunk: the NACK it provokes is only generated
    # by the final write, after which the old client stopped listening and
    # proceeded to apply an incomplete transfer.
    transport.drop_chunks = {5}
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary


def test_legacy_transfer_recovers_from_dropped_final_chunk(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_SETTLE_TIMEOUT", 0.05)
    binary = make_binary(6 * CHUNK)
    transport = FakeSensorTransport(len(binary), windowed_supported=False)
    # Drop the final chunk: no NACK is ever generated, so only the
    # completion probe can heal the transfer.
    transport.drop_chunks = {6}
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary


def test_windowed_transfer_stall_limit_is_wall_clock(monkeypatch):
    """Rewinds keep coming as long as the deadline allows, however many; the
    transfer is failed by elapsed time without progress, not by a count."""
    monkeypatch.setattr("anura.avss.client.PROGRAM_STALL_TIMEOUT", 0.005)
    monkeypatch.setattr("anura.avss.client.PROGRAM_PROGRESS_TIMEOUT", 0.3)
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary), strict_window=False)
    transport.drop_acks = set(range(2, 10_000))
    client = AVSSClient(transport)

    with pytest.raises(AVSSProgramTransferError, match="no progress for"):
        run(procedures.upload_firmware(client, binary, image=0))

    # Far more than the 15 rewinds the old count-based limit allowed.
    assert transport.write_count > 20 * 10


def test_legacy_transfer_fails_when_node_never_accepts_data(monkeypatch):
    """A legacy node that NACKs every write back to the same offset used to
    keep the transfer circling forever; it is now bounded by the deadline."""
    monkeypatch.setattr("anura.avss.client.PROGRAM_PROGRESS_TIMEOUT", 0.2)
    binary = make_binary(6 * CHUNK)
    transport = FakeSensorTransport(len(binary), windowed_supported=False)
    transport.legacy_nack_all = True
    client = AVSSClient(transport)

    with pytest.raises(AVSSProgramTransferError, match="no progress for"):
        run(procedures.upload_firmware(client, binary, image=0))


def test_windowed_transfer_retries_write_timed_out_by_transport():
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary))
    transport.timeout_writes = {4}
    client = AVSSClient(transport)
    progress = []

    run(procedures.upload_firmware(client, binary, image=0, progress=progress.append))

    assert transport.ready
    assert bytes(transport.received) == binary
    assert progress[-1] == len(binary)
    # The timed-out write was never sent, so exactly one extra attempt; the
    # ack for chunk 3 was in the queue, so no rewind was needed either.
    assert transport.write_count == 11


def test_windowed_transfer_fails_when_writes_keep_timing_out(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_STALL_TIMEOUT", 0.01)
    monkeypatch.setattr("anura.avss.client.PROGRAM_PROGRESS_TIMEOUT", 0.1)
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary), strict_window=False)
    transport.timeout_writes = set(range(2, 10_000))
    client = AVSSClient(transport)

    # Whether the deadline trips on the write timeout itself or on the
    # silence check that follows it is a matter of timing.
    with pytest.raises(AVSSProgramTransferError, match="Program transfer stalled"):
        run(procedures.upload_firmware(client, binary, image=0))

    assert transport.write_count > 2  # it did retry


def test_windowed_transfer_write_timeout_after_disconnect():
    """A timed-out write on a transport that has since closed is reported
    as the disconnection it is, not retried against a dead connection."""
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary))
    transport.timeout_writes = {4}
    client = AVSSClient(transport)

    original = transport.program_write

    async def program_write(value):
        if transport.write_count + 1 == 4:
            transport.disconnect()
        await original(value)

    transport.program_write = program_write  # type: ignore[method-assign]

    with pytest.raises(AVSSConnectionError):
        run(procedures.upload_firmware(client, binary, image=0))


def test_legacy_transfer_retries_write_timed_out_by_transport(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_SETTLE_TIMEOUT", 0.05)
    binary = make_binary(6 * CHUNK)
    transport = FakeSensorTransport(len(binary), windowed_supported=False)
    # A data chunk and the first completion probe both time out.
    transport.timeout_writes = {3, 7}
    client = AVSSClient(transport)
    progress = []

    run(procedures.upload_firmware(client, binary, image=0, progress=progress.append))

    assert transport.ready
    assert bytes(transport.received) == binary
    assert progress[-1] == len(binary)
    # 6 chunks + 1 retry, 2 probes + 1 retry.
    assert transport.write_count == 10


def test_legacy_transfer_fails_when_writes_keep_timing_out(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_PROGRESS_TIMEOUT", 0.1)
    binary = make_binary(6 * CHUNK)
    transport = FakeSensorTransport(len(binary), windowed_supported=False)
    transport.timeout_writes = set(range(2, 10_000))
    client = AVSSClient(transport)

    with pytest.raises(AVSSProgramTransferError, match="Program transfer stalled"):
        run(procedures.upload_firmware(client, binary, image=0))


def test_program_transfer_keeps_1_0_contract(monkeypatch):
    # The 1.0 calling convention: prepare_upgrade followed by
    # program_transfer with its original signature.
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_SETTLE_TIMEOUT", 0.05)
    binary = make_binary(4 * CHUNK + 9)
    transport = FakeSensorTransport(len(binary))
    client = AVSSClient(transport)
    progress = []

    async def flow():
        await client.prepare_upgrade(0, len(binary))
        await client.program_transfer(binary, 243, progress.append)

    run(flow())

    assert transport.ready
    assert not transport.windowed
    assert bytes(transport.received) == binary
    assert progress[-1] == len(binary)


def test_transfer_deadline_does_not_start_before_the_program_lock(monkeypatch):
    """asyncio.timeout fixes its deadline when constructed, not when entered.
    Built before the program lock was held, a transfer queued behind another
    would have spent part of its own first window waiting for it, and a long
    enough wait failed it before it had written anything."""
    monkeypatch.setattr("anura.avss.client.PROGRAM_PROGRESS_TIMEOUT", 0.1)
    binary = make_binary(4 * CHUNK)
    transport = FakeSensorTransport(len(binary))
    client = AVSSClient(transport)

    async def scenario():
        await client._program_lock.acquire()

        async def release_after_a_whole_window():
            await asyncio.sleep(0.3)
            client._program_lock.release()

        releasing = asyncio.create_task(release_after_a_whole_window())
        await procedures.upload_firmware(client, binary, image=0)
        await releasing

    run(scenario())

    assert bytes(transport.received) == binary


class OverrunLegacySensor(FakeSensorTransport):
    """A legacy node with a few receive buffers, drained one at a time.

    Buffers drain serially, as the firmware writes them to flash one after
    the other. A write that arrives while every buffer is busy is dropped
    without a word, as the firmware's pool overflow does; the write after it
    is out of order and draws a NACK. NACKs reach the client after ``nack_delay``, so
    writes already issued behind the overrun each draw one of their own.
    Each write takes ``write_latency`` to be accepted, like the request
    round trip of a real transport, which bounds how many writes queue up
    behind an overrun before its NACK lands.
    """

    def __init__(
        self,
        image_size,
        *,
        buffers=3,
        drain_time=0.02,
        nack_delay=0.005,
        write_latency=0.002,
    ):
        super().__init__(image_size, windowed_supported=False)
        self.buffers = buffers
        self.drain_time = drain_time
        self.nack_delay = nack_delay
        self.write_latency = write_latency
        self.inflight: list[float] = []
        self.last_free = 0.0
        self.write_times: list[float] = []
        self.overruns = 0
        self.nacks_sent = 0

    def set_program_callback(self, callback):
        def delayed(data):
            self.nacks_sent += 1
            asyncio.get_running_loop().call_later(self.nack_delay, callback, data)

        self._program_cb = delayed

    def drain_time_for(self, accepted: int) -> float:
        return self.drain_time

    async def program_write(self, value):
        await asyncio.sleep(self.write_latency)
        now = asyncio.get_running_loop().time()
        self.write_times.append(now)
        self.inflight = [t for t in self.inflight if t > now]
        (offset,) = struct.unpack("<L", value[:4])
        if not self.ready and offset == self.expected:
            if len(self.inflight) >= self.buffers:
                self.overruns += 1
                self.write_count += 1
                return
            self.last_free = max(now, self.last_free) + self.drain_time_for(
                self.expected
            )
            self.inflight.append(self.last_free)
        await super().program_write(value)


def test_legacy_transfer_runs_unpaced_on_a_clean_link(monkeypatch, caplog):
    """No NACK, no delay: the transport's back-pressure is the only pacing,
    so a clean transfer is not slowed by a fixed wait per write."""
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_SETTLE_TIMEOUT", 0.05)
    chunks = 100
    binary = make_binary(chunks * CHUNK)
    transport = FakeSensorTransport(len(binary), windowed_supported=False)
    client = AVSSClient(transport)

    start = time.monotonic()
    with caplog.at_level("INFO", logger="anura.avss.client"):
        run(procedures.upload_firmware(client, binary, image=0))
    elapsed = time.monotonic() - start

    assert transport.ready
    assert bytes(transport.received) == binary
    assert transport.write_count == chunks + 2  # chunks + completion probes
    # The old loop waited 40 ms for a NACK before every write: 4 s here.
    assert elapsed < 1.5
    assert "NACK" not in caplog.text


def test_legacy_transfer_backs_off_when_the_node_is_overrun(monkeypatch, caplog):
    """A node that is overrun NACKs; the loop rewinds, adds a write delay and
    converges on the node's drain rate instead of circling."""
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_SETTLE_TIMEOUT", 0.05)
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_NACK_SETTLE", 0.02)
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_BACKOFF_MAX", 0.02)
    chunks = 40
    binary = make_binary(chunks * CHUNK)
    transport = OverrunLegacySensor(len(binary), buffers=3, drain_time=0.02)
    client = AVSSClient(transport)

    with caplog.at_level("INFO", logger="anura.avss.client"):
        run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary
    # The overrun did happen, and the loop recovered from every one.
    assert transport.overruns > 0
    assert transport.nacks_sent > 0
    # Backoff converged: far fewer retransmissions than chunks.
    assert transport.write_count < 2 * chunks
    # The delay saturated at the (lowered) maximum, which is reported as a
    # warning: with working transport back-pressure no NACK should arise.
    assert any(
        r.levelname == "WARNING" and "NACKs" in r.message for r in caplog.records
    )


def test_legacy_transfer_takes_a_nack_burst_as_one_rewind(monkeypatch):
    """The writes queued behind an overrun each draw a NACK for the same
    offset; the loop settles the burst and rewinds once, not once per NACK."""
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_SETTLE_TIMEOUT", 0.05)
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_NACK_SETTLE", 0.02)
    chunks = 12
    binary = make_binary(chunks * CHUNK)
    # Buffers drain slowly enough that the fourth write overruns, and the
    # NACK is late enough that several more writes queue behind it.
    transport = OverrunLegacySensor(len(binary), buffers=3, nack_delay=0.01)
    # Only the first three chunks take time to drain, so exactly one overrun
    # occurs; they have all drained by the time the rewind lands.
    drains = iter([0.01] * 3 + [0.0] * 1000)
    transport.drain_time_for = lambda accepted: next(drains)
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary
    assert transport.overruns >= 1
    assert transport.nacks_sent >= 2
    # One overrun costs one retransmission of the dropped chunk plus the
    # out-of-order writes issued before the first NACK landed, and nothing
    # more: every NACK of the burst names the same offset.
    retransmissions = transport.write_count - 2 - chunks
    assert 1 <= retransmissions <= transport.nacks_sent + 1


def test_legacy_transfer_backoff_decays_once_writes_go_through(monkeypatch):
    """After an overrun the write delay grows; once writes go through
    cleanly it decays back to nothing, so a transient does not slow the
    rest of the transfer."""
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_SETTLE_TIMEOUT", 0.05)
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_NACK_SETTLE", 0.02)
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_BACKOFF_MIN", 0.01)
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_BACKOFF_MAX", 0.04)
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_BACKOFF_RECOVERY", 4)
    chunks = 40
    binary = make_binary(chunks * CHUNK)
    transport = OverrunLegacySensor(len(binary), buffers=3, nack_delay=0.005)
    # The node drains slowly for its first few chunks, then instantly.
    transport.drain_time_for = lambda accepted: 0.05 if accepted < 6 * CHUNK else 0.0
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary
    assert transport.nacks_sent > 0
    # The tail of the transfer ran without any write delay: gaps between the
    # last writes are event-loop noise, well under the minimum backoff.
    tail = transport.write_times[-10:-2]  # exclude the settle-timed probes
    gaps = [b - a for a, b in pairwise(tail)]
    assert max(gaps) < 0.005, gaps


def test_legacy_transfer_write_delay_never_blocks_a_rewind(monkeypatch):
    """A NACK arriving during the write delay is acted on at once, not after
    the delay has run its course."""
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_SETTLE_TIMEOUT", 0.05)
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_NACK_SETTLE", 0.02)
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_BACKOFF_MIN", 0.2)
    monkeypatch.setattr("anura.avss.client.PROGRAM_LEGACY_BACKOFF_MAX", 0.2)
    chunks = 8
    binary = make_binary(chunks * CHUNK)
    # Never overrun; NACKs take 50 ms to arrive.
    transport = OverrunLegacySensor(len(binary), buffers=1000, nack_delay=0.05)
    # Write 2 (chunk 1) is dropped: its NACKs land after the whole first
    # pass has been written, and impose the 200 ms write delay. Write 11
    # (chunk 3 of the second pass) is dropped too: that NACK lands 50 ms
    # into the delay before write 13.
    transport.drop_chunks = {2, 11}
    client = AVSSClient(transport)

    run(procedures.upload_firmware(client, binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary
    # 8 chunks, 4 after the rewind to chunk 1 (writes 9 to 12, write 11
    # dropped), 5 after the rewind to chunk 3, 2 probes.
    assert transport.write_count == 8 + 4 + 5 + 2, transport.write_count
    t = transport.write_times
    # The rewound chunk is written as soon as the burst has settled.
    assert t[8] - t[7] < 0.15, t[8] - t[7]
    # Writes 10 to 12 each waited the full 200 ms for a NACK that never came.
    assert t[9] - t[8] >= 0.2 and t[10] - t[9] >= 0.2 and t[11] - t[10] >= 0.2
    # Write 13 followed the NACK of write 12: 50 ms delivery plus the 20 ms
    # settle, well short of the 200 ms delay it interrupted.
    assert t[12] - t[11] < 0.15, t[12] - t[11]
