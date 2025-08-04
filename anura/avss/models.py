from dataclasses import dataclass
from typing import Any, Union

from anura.marshalling import cbor_field


@dataclass
class ReportSnippetArgs:
    count: int = cbor_field(0)
    auto_resume: bool = cbor_field(1)


@dataclass
class ReportAggregatesArgs:
    count: int = cbor_field(0)
    auto_resume: bool = cbor_field(1)


@dataclass
class ReportCaptureArgs:
    count: int = cbor_field(0)
    auto_resume: bool = cbor_field(1)


@dataclass
class ReportHealthArgs:
    count: Union[bool, int] = cbor_field(0)


@dataclass
class ReportSettings:
    current: bool = cbor_field(0)
    pending: bool = cbor_field(1)


@dataclass
class PrepareUpgradeArgs:
    image: int = cbor_field(0)
    size: int = cbor_field(1)


@dataclass
class ApplyUpgradeArgs:
    pass


@dataclass
class ConfirmUpgradeArgs:
    image: int = cbor_field(0)


@dataclass
class TestThroughputArgs:
    duration: int = cbor_field(0)


@dataclass
class ApplySettingsArgs:
    persist: int = cbor_field(0)


@dataclass
class DeactivateArgs:
    key: int = cbor_field(0)


@dataclass
class TriggerMeasurementArgs:
    duration_ms: int = cbor_field(0)


@dataclass
class ApplySettingsResponse:
    will_reboot: bool = cbor_field(0)


@dataclass
class WriteSettingsResponse:
    num_unhandled: int = cbor_field(0)


@dataclass
class WriteSettingsV2Args:
    settings: dict[int, Any] = cbor_field(0)
    reset_defaults: bool = cbor_field(1)
    apply: bool = cbor_field(2)


@dataclass
class WriteSettingsV2Response:
    num_unhandled: int = cbor_field(0)
    will_reboot: bool = cbor_field(1)


@dataclass
class GetVersionResponse:
    version: str = cbor_field(0)
    build_version: str = cbor_field(1)


@dataclass
class GetFirmwareInfoResponse:
    app_version: int = cbor_field(0)
    app_build_version: str = cbor_field(1)
    app_status: int = cbor_field(2)
    net_version: int = cbor_field(3)
    net_build_version: str = cbor_field(4)


@dataclass
class SnippetReport:
    start_time: int = cbor_field(0)
    sample_rate: float = cbor_field(1)
    range_: int = cbor_field(2)
    samples: dict[int, bytes] = cbor_field(3)
    is_synced: bool = cbor_field(4)


@dataclass
class CaptureReport:
    start_time: int = cbor_field(0)
    unused_key: int = cbor_field(1)
    range_: int = cbor_field(2)
    samples: dict[int, bytes] = cbor_field(3)
    is_synced: bool = cbor_field(4)
    duration: bool = cbor_field(5)
    start_time_monotonic: int = cbor_field(6)
    duration_monotonic: int = cbor_field(7)


@dataclass
class AggregatedValuesReport:
    start_time: int = cbor_field(0)
    values: dict[int, float] = cbor_field(2)


@dataclass
class HealthReport:
    uptime: int = cbor_field(0)
    reboot_count: int = cbor_field(1)
    reset_cause: int = cbor_field(2)
    temperature: float = cbor_field(3)
    battery_voltage: int = cbor_field(4)
    rssi: int = cbor_field(5)
    eh_voltage: int = cbor_field(6)
    clock_sync_skew: float | None = cbor_field(7, default=None)
    clock_sync_age: int | None = cbor_field(8, default=None)
    clock_sync_diff: int | None = cbor_field(9, default=None)


@dataclass
class SettingsReport:
    settings: dict | None = cbor_field(0, default=None)
    pending_settings: dict | None = cbor_field(1, default=None)
