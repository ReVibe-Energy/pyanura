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
