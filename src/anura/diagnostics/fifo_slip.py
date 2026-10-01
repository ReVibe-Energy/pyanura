"""Decoders for the sensor_node FIFO frame-slip detector dumps.

Layouts mirror ``applications/sensor_node/include/fifo_slip.h`` in the anura
firmware: little-endian, packed, each starting with a 4-byte magic and a version.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

RECORD_MAGIC = b"FSR1"
STATS_MAGIC = b"FSS1"
TAIL_BYTES = 64
BATCH_BYTES = 1024
PACKET_BYTES = 8

_RECORD_HEAD = struct.Struct("<4sIIIqIiiiiIIII")
_STATS = struct.Struct("<4sIqIIIIIiiII")


@dataclass
class FifoSlipRecord:
    detected: bool
    reboot_count: int
    uptime_ms: int
    batch_no: int
    count_before: int
    count_after: int
    header_offset: int
    first_bad: int
    bad_packets: int
    int1_events: int
    int1_during: int
    burst_us: int
    prev_tail: bytes
    batch: bytes

    @staticmethod
    def decode(data: bytes) -> FifoSlipRecord:
        if len(data) < _RECORD_HEAD.size + TAIL_BYTES + BATCH_BYTES:
            raise ValueError(f"record too short: {len(data)} bytes")
        (
            magic,
            version,
            detected,
            reboot_count,
            uptime_ms,
            batch_no,
            count_before,
            count_after,
            header_offset,
            first_bad,
            bad_packets,
            int1_events,
            int1_during,
            burst_us,
        ) = _RECORD_HEAD.unpack_from(data)
        if magic != RECORD_MAGIC or version != 1:
            raise ValueError(f"unexpected record magic/version {magic!r}/{version}")
        off = _RECORD_HEAD.size
        return FifoSlipRecord(
            detected=bool(detected),
            reboot_count=reboot_count,
            uptime_ms=uptime_ms,
            batch_no=batch_no,
            count_before=count_before,
            count_after=count_after,
            header_offset=header_offset,
            first_bad=first_bad,
            bad_packets=bad_packets,
            int1_events=int1_events,
            int1_during=int1_during,
            burst_us=burst_us,
            prev_tail=bytes(data[off : off + TAIL_BYTES]),
            batch=bytes(data[off + TAIL_BYTES : off + TAIL_BYTES + BATCH_BYTES]),
        )

    def shifted_tail_score(self) -> float | None:
        """Fraction of packets after ``first_bad`` whose bytes look like the
        previous sensor byte shifted left by one bit (a clock glitch signature).

        Returns None when no bad packet was recorded.
        """
        if self.first_bad < 0:
            return None
        start = self.first_bad * PACKET_BYTES
        tail = self.batch[start:]
        if len(tail) < 2 * PACKET_BYTES:
            return None
        hits = 0
        total = 0
        for i in range(0, len(tail) - PACKET_BYTES, PACKET_BYTES):
            # A valid accel header is 0x4x; after a one-bit shift it reads 0x8x/0x9x.
            total += 1
            if (tail[i] & 0xE0) in (0x80, 0xA0):
                hits += 1
        return hits / total if total else None


@dataclass
class FifoSlipStats:
    uptime_ms: int
    batches: int
    misaligned: int
    bad_header_batches: int
    int1_multi: int
    int1_during: int
    count_before_min: int
    count_before_max: int
    burst_us_max: int
    detected: bool

    @staticmethod
    def decode(data: bytes) -> FifoSlipStats:
        if len(data) < _STATS.size:
            raise ValueError(f"stats too short: {len(data)} bytes")
        (
            magic,
            version,
            uptime_ms,
            batches,
            misaligned,
            bad_header_batches,
            int1_multi,
            int1_during,
            cb_min,
            cb_max,
            burst_us_max,
            detected,
        ) = _STATS.unpack_from(data)
        if magic != STATS_MAGIC or version != 1:
            raise ValueError(f"unexpected stats magic/version {magic!r}/{version}")
        return FifoSlipStats(
            uptime_ms=uptime_ms,
            batches=batches,
            misaligned=misaligned,
            bad_header_batches=bad_header_batches,
            int1_multi=int1_multi,
            int1_during=int1_during,
            count_before_min=cb_min,
            count_before_max=cb_max,
            burst_us_max=burst_us_max,
            detected=bool(detected),
        )
