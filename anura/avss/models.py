from dataclasses import dataclass
from anura.dataclasses_cbor import (dataclass_cbor, field)
from typing import Union

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
class ReportCaptureArgs:
    count: int = field(0)

@dataclass_cbor()
@dataclass
class ReportHealthArgs:
    count: Union[bool, int] = field(0)

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
class TestThroughputArgs:
    duration: int = field(0)

@dataclass_cbor()
@dataclass
class ApplySettingsArgs:
    persist: int = field(0)

@dataclass_cbor()
@dataclass
class DeactivateArgs:
    key: int = field(0)

@dataclass_cbor()
@dataclass
class ApplySettingsResponse:
    will_reboot: bool = field(0)

@dataclass_cbor()
@dataclass
class WriteSettingsResponse:
    num_unhandled: int = field(0)

@dataclass_cbor()
@dataclass
class GetVersionResponse:
    version: str = field(0)
    build_version: str = field(1)

@dataclass_cbor()
@dataclass
class SnippetReport:
    start_time: int = field(0)
    sample_rate: int = field(1)
    range_: int = field(2)
    samples: dict[int, bytes] = field(3)
    is_synced: bool = field(4)

@dataclass_cbor()
@dataclass
class CaptureReport:
    start_time: int = field(0)
    end_time: int = field(1)
    range_: int = field(2)
    samples: dict[int, bytes] = field(3)

@dataclass_cbor()
@dataclass
class AggregatedValuesReport:
    start_time: int = field(0)
    duration: int = field(1)
    values: dict[int, int] = field(2)

@dataclass_cbor()
@dataclass
class HealthReport:
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
class SettingsReport:
    settings: dict = field(0)
