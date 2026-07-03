"""Tests for the Program characteristic firmware transfer loops."""

import asyncio
import hashlib
import struct

import cbor2
import pytest

from anura.avss.client import PROGRAM_OFFSET_ABORT, AVSSClient
from anura.avss.exceptions import AVSSProgramTransferError
from anura.avss.protocol import OpCode, ResponseCode
from anura.avss.transport.base import AVSSTransport


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
        self.abort_at_write: int | None = None

        # Bookkeeping for assertions
        self.write_count = 0
        self.ack_count = 0
        self.delivered_acked = 0
        self.pending_ends: list[int] = []
        self.payload_sizes: list[int] = []

        self._program_cb = None

    async def open(self):
        pass

    async def close(self):
        pass

    def set_report_callback(self, callback):
        pass

    def set_program_callback(self, callback):
        self._program_cb = callback

    def set_closed_callback(self, callback):
        pass

    def _notify(self, acked, window):
        self.ack_count += 1
        if self.ack_count not in self.drop_acks:
            self.delivered_acked = max(self.delivered_acked, acked)
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

    async def control_point_request(self, req):
        opcode = req[0]
        args = cbor2.loads(req[1:])
        if opcode == OpCode.PREPARE_UPGRADE_V2:
            if not self.windowed_supported:
                return bytes([OpCode.RESPONSE, opcode, ResponseCode.OPCODE_UNSUPPORTED])
            assert args[1] == self.image_size
            assert len(args[2]) == 32
            resume = (
                self.digest == args[2]
                and self.prepared_image == args[0]
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
                self._fresh_prepare(args[0], args[2])
            self.windowed = True
            self._send_ack()  # initial window grant
            return bytes([OpCode.PREPARE_UPGRADE_V2_RESPONSE]) + cbor2.dumps(
                {0: self.max_chunk, 1: self.window_chunks, 2: self.expected}
            )
        if opcode == OpCode.PREPARE_UPGRADE:
            assert args[1] == self.image_size
            self._fresh_prepare(args[0], None)
            self.windowed = False
            return bytes([OpCode.RESPONSE, opcode, ResponseCode.OK])
        raise AssertionError(f"unexpected opcode {opcode}")

    async def program_write(self, value):
        self.write_count += 1
        (offset,) = struct.unpack("<L", value[:4])
        payload = value[4:]
        self.payload_sizes.append(len(payload))
        assert len(payload) <= self.max_chunk

        if self.aborted:
            return

        if self.abort_at_write == self.write_count:
            self.aborted = True
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
            if offset != self.expected:
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

    run(client.program_transfer(binary, image=0, progress=progress.append))

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

    run(client.program_transfer(binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary
    assert max(transport.payload_sizes) <= 100


def test_windowed_transfer_single_chunk():
    binary = make_binary(10)
    transport = FakeSensorTransport(len(binary))
    client = AVSSClient(transport)

    run(client.program_transfer(binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary


def test_windowed_transfer_recovers_from_dropped_chunk():
    binary = make_binary(20 * CHUNK)
    transport = FakeSensorTransport(len(binary), strict_window=False)
    transport.drop_chunks = {5}
    client = AVSSClient(transport)

    run(client.program_transfer(binary, image=0))

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

    run(client.program_transfer(binary, image=0))

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

    run(client.program_transfer(binary, image=0))

    assert transport.ready
    assert bytes(transport.received) == binary


def test_windowed_transfer_aborted_by_node():
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary))
    transport.abort_at_write = 4
    client = AVSSClient(transport)

    with pytest.raises(AVSSProgramTransferError):
        run(client.program_transfer(binary, image=0))


def test_windowed_transfer_fails_on_persistent_stall(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_STALL_TIMEOUT", 0.01)
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary), strict_window=False)
    # Drop every notification after the initial grant.
    transport.drop_acks = set(range(2, 10_000))
    client = AVSSClient(transport)

    with pytest.raises(AVSSProgramTransferError):
        run(client.program_transfer(binary, image=0))


def test_windowed_transfer_resumes_after_interruption(monkeypatch):
    monkeypatch.setattr("anura.avss.client.PROGRAM_STALL_TIMEOUT", 0.01)
    binary = make_binary(40 * CHUNK)
    transport = FakeSensorTransport(len(binary), strict_window=False)
    client = AVSSClient(transport)

    # First attempt is starved of feedback and fails with partial progress.
    transport.drop_acks = set(range(2, 10_000))
    with pytest.raises(AVSSProgramTransferError):
        run(client.program_transfer(binary, image=0))
    committed = transport.expected
    assert 0 < committed < len(binary)

    # Second attempt resumes from the committed offset.
    transport.drop_acks = set()
    progress = []
    run(client.program_transfer(binary, image=0, progress=progress.append))

    assert transport.resumed_from == committed
    assert transport.ready
    assert bytes(transport.received) == binary
    # The resumed transfer starts progress reporting at the resume offset.
    assert progress[0] == committed


def test_windowed_transfer_resume_of_complete_transfer():
    binary = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary))
    client = AVSSClient(transport)

    run(client.program_transfer(binary, image=0))
    writes = transport.write_count

    progress = []
    run(client.program_transfer(binary, image=0, progress=progress.append))

    assert transport.resumed_from == len(binary)
    assert transport.write_count == writes  # nothing retransmitted
    assert progress == [len(binary)]


def test_windowed_transfer_different_image_restarts():
    binary_a = make_binary(10 * CHUNK)
    transport = FakeSensorTransport(len(binary_a))
    client = AVSSClient(transport)

    run(client.program_transfer(binary_a, image=0))

    binary_b = bytes(reversed(binary_a))
    run(client.program_transfer(binary_b, image=0))

    assert transport.resumed_from is None  # fresh prepare, not a resume
    assert transport.ready
    assert bytes(transport.received) == binary_b


def test_legacy_fallback_transfer_completes():
    binary = make_binary(5 * CHUNK + 3)
    transport = FakeSensorTransport(len(binary), windowed_supported=False)
    client = AVSSClient(transport)
    progress = []

    run(client.program_transfer(binary, image=0, progress=progress.append))

    assert transport.ready
    assert not transport.windowed
    assert bytes(transport.received) == binary
    assert progress[-1] == len(binary)
