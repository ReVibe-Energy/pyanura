"""Tests for SettingsMapper value coercion to firmware wire types."""

import cbor2
import pytest

from anura.avss.settings import SettingsMapper, SettingType


def test_types_cover_every_setting():
    # The type table must stay in lockstep with forward_map so no setting is
    # ever sent without a known wire type.
    assert set(SettingsMapper.types) == set(SettingsMapper.forward_map)


def test_integral_float_coerced_to_uint():
    result = SettingsMapper.from_readable(
        {"base_sample_rate_hz": 2048.0, "snippet_length": 3096.0}
    )

    assert result == {0: 2048, 2: 3096}
    assert all(type(v) is int for v in result.values())


def test_int_coerced_to_float_setting():
    result = SettingsMapper.from_readable({"wom_threshold_g": 1})

    assert result == {8: 1.0}
    assert type(result[8]) is float
    assert type(cbor2.loads(cbor2.dumps(result))[8]) is float


def test_non_integral_float_for_uint_rejected():
    with pytest.raises(ValueError, match="expects an integer"):
        SettingsMapper.from_readable({"snippet_length": 3096.5})


def test_bool_rejected_for_uint_setting():
    # bool is a subclass of int, but a bool must never be sent where the
    # firmware expects a numeric type.
    with pytest.raises(TypeError, match="expects an int"):
        SettingsMapper.from_readable({"base_sample_rate_hz": True})


def test_bool_rejected_for_float_setting():
    with pytest.raises(TypeError, match="expects a float"):
        SettingsMapper.from_readable({"wom_threshold_g": False})


def test_int_rejected_for_bool_setting():
    with pytest.raises(TypeError, match="expects a bool"):
        SettingsMapper.from_readable({"events_motion_start_enable": 1})


def test_float_rejected_for_bool_setting():
    with pytest.raises(TypeError, match="expects a bool"):
        SettingsMapper.from_readable({"events_motion_start_enable": 1.0})


def test_bool_accepted_for_bool_setting():
    result = SettingsMapper.from_readable(
        {"events_motion_start_enable": True, "events_motion_start_capture": False}
    )

    assert result == {12: True, 13: False}
    assert all(type(v) is bool for v in result.values())


def test_unknown_key_passes_through_unchanged():
    # A raw integer key not in forward_map has no known type and is left as-is.
    assert SettingsMapper.from_readable({9999: 5.0}) == {9999: 5.0}


def test_settings_type_is_exhaustive():
    # Guard against adding a wire type without handling it in _coerce.
    assert set(SettingType) == {
        SettingType.UINT,
        SettingType.FLOAT,
        SettingType.BOOL,
    }
