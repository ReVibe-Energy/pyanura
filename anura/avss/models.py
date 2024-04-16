from dataclasses import dataclass
from anura.dataclasses_cbor import (dataclass_cbor, field)

@dataclass_cbor()
@dataclass
class ReportSnippetArgs:
    count: int = field(0)
    auto_resume: bool = field(1)

@dataclass_cbor()
@dataclass
class ReportAggregatesArgs:
    count: int = field(0)
    auto_resume: bool = field(1)

@dataclass_cbor()
@dataclass
class ReportHealthArgs:
    active: bool = field(0)

@dataclass_cbor()
@dataclass
class ReportSettings:
    pass

@dataclass_cbor()
@dataclass
class PrepareUpgradeArgs:
    image: int = field(0)
    size: int = field(1)

@dataclass_cbor()
@dataclass
class ApplyUpgradeArgs:
    pass

@dataclass_cbor()
@dataclass
class ConfirmUpgradeArgs:
    image: int = field(0)

@dataclass_cbor()
@dataclass
class SettingsGroup:
    sample_rate: int = field(0)
    snippet_interval: int = field(1)
    snippet_length: int = field(2)
    health_interval: int = field(3)

@dataclass_cbor()
@dataclass
class WriteSettingsArgs:
    sample_rate: int = field(0)
    snippet_interval: int = field(1)
    snippet_length: int = field(2)
    health_interval: int = field(3)

@dataclass_cbor()
@dataclass
class TestThroughputArgs:
    duration: int = field(0)

@dataclass_cbor()
@dataclass
class ApplySettingsArgs:
    persist: int = field(0)

@dataclass_cbor()
@dataclass
class WriteSettingsResponse:
    num_unhandled: int = field(0)

@dataclass_cbor()
@dataclass
class GetVersionResponse:
    version: str = field(0)
    build_version: str = field(1)

class Report:
    def parse(record):
        report_type = record[0]
        payload = record[1:]

        report_classes = {
            2: SnippetReport,
            3: AggregatedValuesReport,
            4: HealthReport,
            5: SettingsReport,
        }
        if report_class := report_classes.get(report_type):
            return report_class.from_cbor(payload)
        else:
            return UnknownReport(report_type, payload)

class UnknownReport(Report):
    def __init__(self, report_type, payload):
        self.report_type = report_type
        self.payload = payload

@dataclass_cbor()
@dataclass
class SnippetReport(Report):
    start_time: int = field(0)
    sample_rate: int = field(1)
    range_: int = field(2)
    samples: dict[int, bytes] = field(3)

@dataclass_cbor()
@dataclass
class AggregatedValuesReport(Report):
    start_time: int = field(0)
    duration: int = field(1)
    values: dict[int, int] = field(2)

@dataclass_cbor()
@dataclass
class HealthReport(Report):
    uptime: int = field(0)
    reboot_count: int = field(1)
    reset_cause: int = field(2)
    temperature: float = field(3)
    battery_voltage: int = field(4)
    rssi: int = field(5)
    eh_voltage: int = field(6)
    clock_sync_skew: float = field(7)
    clock_sync_age: int = field(8)
    clock_sync_diff: int = field(9)

@dataclass_cbor()
@dataclass
class SettingsReport(Report):
    settings: SettingsGroup = field(0)
