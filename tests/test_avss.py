from anura.avss.models import (
    HealthReport,
    SnippetReport,
)
from anura.marshalling import unmarshal


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


def test_unlimited_report_count_encodes_as_null():
    from anura.avss.models import (
        UNLIMITED,
        ReportAggregatesArgs,
        ReportCaptureArgs,
        ReportHealthArgs,
        ReportSnippetArgs,
    )
    from anura.marshalling import marshal

    # The node requires key 0 to be present; null means unlimited.
    for cls in (ReportSnippetArgs, ReportAggregatesArgs, ReportCaptureArgs):
        assert marshal(cls(count=UNLIMITED, auto_resume=True)) == {0: None, 1: True}
        assert marshal(cls(count=3, auto_resume=False)) == {0: 3, 1: False}
    assert marshal(ReportHealthArgs(count=UNLIMITED)) == {0: None}
    assert marshal(ReportHealthArgs(count=True)) == {0: True}


def test_report_args_round_trip():
    from anura.avss.models import (
        UNLIMITED,
        ReportAggregatesArgs,
        ReportCaptureArgs,
        ReportHealthArgs,
        ReportSnippetArgs,
    )
    from anura.marshalling import marshal

    for cls in (ReportSnippetArgs, ReportAggregatesArgs, ReportCaptureArgs):
        for count in (UNLIMITED, 3):
            args = cls(count=count, auto_resume=True)
            assert unmarshal(cls, marshal(args)) == args
    for count in (UNLIMITED, True, 3):
        args = ReportHealthArgs(count=count)
        assert unmarshal(ReportHealthArgs, marshal(args)) == args
