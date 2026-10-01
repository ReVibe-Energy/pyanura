import cbor2
import pytest

from anura.avss.client import Report
from anura.avss.exceptions import AVSSProtocolError
from anura.avss.models import (
    UNLIMITED,
    HealthReport,
    ReportAggregatesArgs,
    ReportCaptureArgs,
    ReportHealthArgs,
    ReportSnippetArgs,
    SnippetReport,
    WriteSettingsV2Response,
)
from anura.avss.protocol import ReportType
from anura.marshalling import marshal, unmarshal


def test_unmarshal_HealthReport_missing_fields():
    # HealthReport can be unmarshalled with keys 7-9 missing.
    unmarshal(
        HealthReport,
        {
            0: 0,
            1: 0,
            2: 0,
            3: 0.0,
            4: 0,
            5: 0,
            6: 0,
        },
    )


def test_unmarshal_SnippetReport_without_timing():
    # Pre-v26.4.0 firmware omits the timing fields (keys 5-8).
    report = unmarshal(
        SnippetReport,
        {
            0: 0,
            1: 1000.0,
            2: 16,
            3: {0: b""},
            4: True,
        },
    )
    assert report.duration is None
    assert report.transmission_offset is None


def test_unmarshal_SnippetReport_with_timing():
    # v26.4.0+ firmware adds keys 5-8.
    report = unmarshal(
        SnippetReport,
        {
            0: 0,
            1: 1000.0,
            2: 16,
            3: {0: b""},
            4: True,
            5: 5,
            6: 6,
            7: 7,
            8: 8,
        },
    )
    assert report.duration == 5
    assert report.transmission_offset == 8


def test_report_count_args_encode_and_round_trip():
    # The node requires key 0 to be present; null means unlimited.
    for cls in (ReportSnippetArgs, ReportAggregatesArgs, ReportCaptureArgs):
        for count, wire in ((UNLIMITED, None), (3, 3)):
            args = cls(count=count, auto_resume=True)
            assert marshal(args) == {0: wire, 1: True}
            assert unmarshal(cls, marshal(args)) == args
    for count, wire in ((UNLIMITED, None), (True, True), (3, 3)):
        args = ReportHealthArgs(count=count)
        assert marshal(args) == {0: wire}
        assert unmarshal(ReportHealthArgs, marshal(args)) == args


def test_report_parse_rejects_a_malformed_payload():
    # A record that decodes but does not end where the CBOR item does means
    # the framing is wrong, so the item that did decode is not trusted.
    payload = cbor2.dumps({0: 0, 1: 0, 2: 0, 3: 0.0, 4: 0, 5: 0, 6: 0})
    record = bytes((ReportType.HEALTH,)) + payload
    Report.from_record(record).parse()
    with pytest.raises(AVSSProtocolError, match="trailing byte"):
        Report.from_record(record + b"\x00").parse()
    with pytest.raises(AVSSProtocolError):
        Report.from_record(record[:-1]).parse()


def test_unmarshal_write_settings_v2_response_without_num_unhandled():
    # Current firmware omits num_unhandled (key 0) from the response.
    response = unmarshal(WriteSettingsV2Response, {1: True})
    assert response.will_reboot is True
    assert response.num_unhandled is None


def test_debug_dump_report_parses_name_and_bytes():
    from anura.avss.models import DebugDumpReport
    from anura.avss.protocol import ReportType

    payload = cbor2.dumps({0: "fifo-stats", 1: b"\x01\x02\x03"})
    report = Report(report_type=int(ReportType.DEBUG_DUMP), payload_cbor=payload)
    parsed = report.parse()
    assert isinstance(parsed, DebugDumpReport)
    assert parsed.name == "fifo-stats"
    assert parsed.data == b"\x01\x02\x03"


def test_debug_dump_report_accepts_hand_encoded_firmware_header():
    """The firmware hand-encodes map(2) {0: tstr, 1: bstr} with a 2-byte bstr length."""
    from anura.avss.models import DebugDumpReport
    from anura.avss.protocol import ReportType

    data = bytes(range(256)) * 5
    payload = (
        b"\xa2\x00"
        + b"\x69fifo-slip"
        + b"\x01\x59"
        + len(data).to_bytes(2, "big")
        + data
    )
    report = Report(report_type=int(ReportType.DEBUG_DUMP), payload_cbor=payload)
    parsed = report.parse()
    assert isinstance(parsed, DebugDumpReport)
    assert parsed.name == "fifo-slip"
    assert parsed.data == data


def test_fifo_slip_record_decoder_round_trip():
    import struct

    from anura.diagnostics.fifo_slip import FifoSlipRecord, FifoSlipStats

    head = struct.pack(
        "<4sIIIqIiiiiIIII",
        b"FSR1",
        1,
        1,
        7,
        123456,
        42,
        1032,
        167,
        7,
        3,
        100,
        1,
        0,
        1050,
    )
    batch = bytes([0x40, 1, 2, 3, 4, 5, 6, 7] * 128)
    rec = FifoSlipRecord.decode(head + bytes(64) + batch)
    assert rec.detected and rec.reboot_count == 7 and rec.count_after == 167
    assert rec.header_offset == 7 and rec.first_bad == 3 and rec.batch == batch

    stats = FifoSlipStats.decode(
        struct.pack(
            "<4sIqIIIIIiiII", b"FSS1", 1, 999, 5000, 0, 0, 1, 0, 1032, 1040, 1100, 0
        )
    )
    assert (
        stats.batches == 5000 and stats.count_before_max == 1040 and not stats.detected
    )
