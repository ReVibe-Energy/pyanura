from dataclasses import dataclass
from typing import Annotated, Any

from anura.marshalling import CborKey


@dataclass
class ReportSnippetArgs:
    count: Annotated[int, CborKey(0)]
    auto_resume: Annotated[bool, CborKey(1)]


@dataclass
class ReportAggregatesArgs:
    count: Annotated[int, CborKey(0)]
    auto_resume: Annotated[bool, CborKey(1)]


@dataclass
class ReportCaptureArgs:
    count: Annotated[int, CborKey(0)]
    auto_resume: Annotated[bool, CborKey(1)]


@dataclass
class ReportHealthArgs:
    count: Annotated[bool | int, CborKey(0)]


@dataclass
class ReportSettings:
    current: Annotated[bool, CborKey(0)]
    pending: Annotated[bool, CborKey(1)]


@dataclass
class PrepareUpgradeArgs:
    image: Annotated[int, CborKey(0)]
    size: Annotated[int, CborKey(1)]


@dataclass
class ApplyUpgradeArgs:
    pass


@dataclass
class ConfirmUpgradeArgs:
    image: Annotated[int, CborKey(0)]


@dataclass
class TestThroughputArgs:
    duration: Annotated[int, CborKey(0)]


@dataclass
class ApplySettingsArgs:
    persist: Annotated[int, CborKey(0)]


@dataclass
class DeactivateArgs:
    key: Annotated[int, CborKey(0)]


@dataclass
class TriggerMeasurementArgs:
    duration_ms: Annotated[int, CborKey(0)]


@dataclass
class TriggerCaptureArgs:
    duration_ms: Annotated[int, CborKey(0)]


@dataclass
class ApplySettingsResponse:
    will_reboot: Annotated[bool, CborKey(0)]


@dataclass
class WriteSettingsResponse:
    num_unhandled: Annotated[int, CborKey(0)]


@dataclass
class WriteSettingsV2Args:
    settings: Annotated[dict[int, Any], CborKey(0)]
    reset_defaults: Annotated[bool, CborKey(1)]
    apply: Annotated[bool, CborKey(2)]


@dataclass
class WriteSettingsV2Response:
    num_unhandled: Annotated[int, CborKey(0)]
    will_reboot: Annotated[bool, CborKey(1)]


@dataclass
class GetVersionResponse:
    version: Annotated[str, CborKey(0)]
    build_version: Annotated[str, CborKey(1)]


@dataclass
class GetFirmwareInfoResponse:
    app_version: Annotated[int, CborKey(0)]
    app_build_version: Annotated[str, CborKey(1)]
    app_status: Annotated[int, CborKey(2)]
    net_version: Annotated[int, CborKey(3)]
    net_build_version: Annotated[str, CborKey(4)]


@dataclass
class SnippetReport:
    start_time: Annotated[int, CborKey(0)]
    sample_rate: Annotated[float, CborKey(1)]
    range_: Annotated[int, CborKey(2)]
    samples: Annotated[dict[int, bytes], CborKey(3)]
    is_synced: Annotated[bool, CborKey(4)]
    duration: Annotated[int | None, CborKey(5)] = None
    start_time_monotonic: Annotated[int | None, CborKey(6)] = None
    duration_monotonic: Annotated[int | None, CborKey(7)] = None
    transmission_offset: Annotated[int | None, CborKey(8)] = None


@dataclass
class CaptureReport:
    start_time: Annotated[int, CborKey(0)]
    range_: Annotated[int, CborKey(2)]
    samples: Annotated[dict[int, bytes], CborKey(3)]
    is_synced: Annotated[bool, CborKey(4)]
    duration: Annotated[int, CborKey(5)]
    start_time_monotonic: Annotated[int, CborKey(6)]
    duration_monotonic: Annotated[int, CborKey(7)]
    transmission_offset: Annotated[int | None, CborKey(8)] = None


@dataclass
class AggregatedValuesReport:
    start_time: Annotated[int, CborKey(0)]
    values: Annotated[dict[int, float], CborKey(2)]


@dataclass
class HealthReport:
    uptime: Annotated[int, CborKey(0)]
    reboot_count: Annotated[int, CborKey(1)]
    reset_cause: Annotated[int, CborKey(2)]
    temperature: Annotated[float, CborKey(3)]
    battery_voltage: Annotated[int, CborKey(4)]
    rssi: Annotated[int, CborKey(5)]
    eh_voltage: Annotated[int, CborKey(6)]
    clock_sync_skew: Annotated[float | None, CborKey(7)] = None
    clock_sync_age: Annotated[int | None, CborKey(8)] = None
    clock_sync_diff: Annotated[int | None, CborKey(9)] = None


@dataclass
class SettingsReport:
    settings: Annotated[dict | None, CborKey(0)] = None
    pending_settings: Annotated[dict | None, CborKey(1)] = None
