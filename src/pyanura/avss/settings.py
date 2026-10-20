import enum
from typing import ClassVar


class SettingType(enum.Enum):
    """The CBOR type the node firmware expects for a setting value."""

    UINT = enum.auto()
    FLOAT = enum.auto()
    BOOL = enum.auto()


class SettingsMapper:
    forward_map: ClassVar[dict[str, int]] = {
        "base_sample_rate_hz": 0,
        "snippet_interval_ms": 1,
        "snippet_length": 2,
        "health_interval_ms": 3,
        "base_axis_enable": 4,
        "motion_threshold_rms_g": 5,
        "motion_standby_delay_ms": 6,
        "wom_sample_rate_hz": 7,
        "wom_threshold_g": 8,
        "snippet_mode": 9,
        "capture_mode": 10,
        "capture_buffer_length": 11,
        "events_motion_start_enable": 12,
        "events_motion_start_capture": 13,
        "events_motion_start_capture_duration_ms": 14,
        "aggregates_mode": 15,
        "aggregates_interval_ms": 16,
        "aggregates_sample_rate_hz": 17,
        "aggregates_hpf_mode": 18,
        "aggregates_hpf_cutoff": 19,
        "aggregates_fft_mode": 20,
        "aggregates_fft_length": 21,
        "aggregates_param_enable_0_31": 22,
        "aggregates_param_enable_32_63": 23,
    }

    reverse_map: ClassVar[dict[int, str]] = {
        key: name for name, key in forward_map.items()
    }

    # The wire type per setting, kept in lockstep with the AVSS protocol
    # and firmware implemenations. Every name in forward_map must appear here.
    types: ClassVar[dict[str, SettingType]] = {
        "base_sample_rate_hz": SettingType.UINT,
        "snippet_interval_ms": SettingType.UINT,
        "snippet_length": SettingType.UINT,
        "health_interval_ms": SettingType.UINT,
        "base_axis_enable": SettingType.UINT,
        "motion_threshold_rms_g": SettingType.FLOAT,
        "motion_standby_delay_ms": SettingType.UINT,
        "wom_sample_rate_hz": SettingType.UINT,
        "wom_threshold_g": SettingType.FLOAT,
        "snippet_mode": SettingType.UINT,
        "capture_mode": SettingType.UINT,
        "capture_buffer_length": SettingType.UINT,
        "events_motion_start_enable": SettingType.BOOL,
        "events_motion_start_capture": SettingType.BOOL,
        "events_motion_start_capture_duration_ms": SettingType.UINT,
        "aggregates_mode": SettingType.UINT,
        "aggregates_interval_ms": SettingType.UINT,
        "aggregates_sample_rate_hz": SettingType.UINT,
        "aggregates_hpf_mode": SettingType.UINT,
        "aggregates_hpf_cutoff": SettingType.FLOAT,
        "aggregates_fft_mode": SettingType.UINT,
        "aggregates_fft_length": SettingType.UINT,
        "aggregates_param_enable_0_31": SettingType.UINT,
        "aggregates_param_enable_32_63": SettingType.UINT,
    }

    @staticmethod
    def _coerce(name, value):
        """Coerce value to the CBOR type the firmware expects for setting name.

        Settings with no known type (e.g. a raw integer key not in
        forward_map) pass through unchanged.
        """
        setting_type = SettingsMapper.types.get(name)
        if setting_type is SettingType.BOOL and not isinstance(value, bool):
            raise TypeError(f"setting {name!r} expects a bool, got {type(value)!r}")
        # bool is a subclass of int, so guard against it explicitly: only
        # int<->float coerce, never bool<->numeric in either direction.
        if setting_type is SettingType.FLOAT:
            if isinstance(value, bool):
                raise TypeError(
                    f"setting {name!r} expects a float, got {type(value)!r}"
                )
            if isinstance(value, int):
                return float(value)
            elif isinstance(value, float):
                return value
            else:
                raise TypeError(
                    f"setting {name!r} expects a float, got {type(value)!r}"
                )
        if setting_type is SettingType.UINT:
            if isinstance(value, bool):
                raise TypeError(f"setting {name!r} expects an int, got {type(value)!r}")
            if isinstance(value, float):
                if not value.is_integer():
                    raise ValueError(
                        f"setting {name!r} expects an integer, got {value!r}"
                    )
                return int(value)
            elif isinstance(value, int):
                return value
            else:
                raise TypeError(f"setting {name!r} expects an int, got {type(value)!r}")
        return value

    @staticmethod
    def from_readable(settings):
        def map_key(key):
            try:
                return SettingsMapper.forward_map[key]
            except KeyError:
                try:
                    return int(key)
                except ValueError:
                    raise ValueError(f"Invalid key {key}") from None

        result = {}
        for key, value in settings.items():
            key_id = map_key(key)
            name = SettingsMapper.reverse_map.get(key_id)
            result[key_id] = SettingsMapper._coerce(name, value)
        return result

    @staticmethod
    def to_readable(settings):
        def map_key(key):
            try:
                return SettingsMapper.reverse_map[key]
            except KeyError:
                return str(key)

        return {map_key(k): v for k, v in settings.items()}
