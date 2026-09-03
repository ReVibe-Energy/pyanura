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
