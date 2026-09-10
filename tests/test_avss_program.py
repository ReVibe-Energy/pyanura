"""Tests for the Program characteristic firmware transfer loops."""

import asyncio
import hashlib
import struct
from collections.abc import Callable

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
