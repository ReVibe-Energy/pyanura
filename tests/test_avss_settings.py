"""Tests for the settings update procedure."""

import asyncio

import cbor2
import pytest

from anura.avss.client import AVSSClient
from anura.avss.procedures import UpdateSettingsResult, update_settings
from anura.avss.procedures._update_settings import (
    _SETTINGS_DEFAULTS,
    _chunk_settings,
    _write_settings_size,
)
from anura.avss.protocol import OpCode, ResponseCode
from anura.avss.settings import SettingsMapper, SettingType
from anura.avss.transport.base import AVSSTransport

# A value no default or written setting uses, marking "unknown prior state".
SENTINEL = 0xDEAD

# Settings known to firmware releases without built-in settings reset.
V24_9_KEYS = frozenset(range(10))  # up to and including v24.9.0
V25_1_KEYS = frozenset(range(15))  # v25.1.0 .. v25.5.0


class FakeNodeTransport(AVSSTransport):
    """Emulates the Control Point settings handling of node firmware.

    Mirrors avss.c: a fixed request buffer, pending settings accumulated
    across Write Settings requests, unknown settings skipped and counted,
    and optionally Write Settings V2 with its built-in reset to defaults
    (whose response omits num_unhandled, like current firmware).

    Applying promotes the pending settings to ``applied``, either through the
    Write Settings V2 apply flag or an Apply Settings request. With
    ``apply_detail`` unset, Apply Settings answers with a bare OK like
    pre-v24.6.0 firmware.
    """

    def __init__(
        self,
        *,
        known_keys,
        defaults,
        v2_supported,
        request_limit,
        apply_detail=True,
        will_reboot=False,
    ):
        self.known_keys = known_keys
        self.defaults = {k: v for k, v in defaults.items() if k in known_keys}
        self.v2_supported = v2_supported
        self.request_limit = request_limit
        self.apply_detail = apply_detail
        self.will_reboot = will_reboot
        self.pending = {k: SENTINEL for k in known_keys}
        self.applied: dict | None = None
        self.requests: list[bytes] = []

    async def open(self):
        pass

    async def close(self):
        pass

    def set_report_callback(self, callback):
        pass

    def set_program_callback(self, callback):
        pass

    def set_closed_callback(self, callback):
        pass

    async def program_write(self, value):
        raise AssertionError("Not a program transfer test")

    async def control_point_request(self, req, *, timeout=None):
        assert len(req) <= self.request_limit, (
            f"{len(req)} byte request exceeds the node's "
            f"{self.request_limit} byte buffer"
        )
        self.requests.append(bytes(req))
        opcode = req[0]
        arg = cbor2.loads(req[1:])

        if opcode == OpCode.WRITE_SETTINGS:
            num_unhandled = self._stage(arg)
            return bytes([OpCode.WRITE_SETTINGS_RESPONSE]) + cbor2.dumps(
                {0: num_unhandled}
            )

        if opcode == OpCode.WRITE_SETTINGS_V2:
            if not self.v2_supported:
                return bytes([OpCode.RESPONSE, opcode, ResponseCode.OPCODE_UNSUPPORTED])
            if arg[1]:  # reset_defaults
                self.pending = dict(self.defaults)
            self._stage(arg[0])
            if arg[2]:  # apply
                self.applied = dict(self.pending)
            return bytes([OpCode.WRITE_SETTINGS_V2_RESPONSE]) + cbor2.dumps(
                {1: self.will_reboot}
            )

        if opcode == OpCode.APPLY_SETTINGS:
            self.applied = dict(self.pending)
            if not self.apply_detail:
                return bytes([OpCode.RESPONSE, opcode, ResponseCode.OK])
            return bytes([OpCode.APPLY_SETTINGS_RESPONSE]) + cbor2.dumps(
                {0: self.will_reboot}
            )

        raise AssertionError(f"Unexpected opcode {opcode}")

    def _stage(self, settings):
        num_unhandled = 0
        for key, value in settings.items():
            if key in self.known_keys:
                self.pending[key] = value
            else:
                num_unhandled += 1
        return num_unhandled


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=30))


DEFAULTS = SettingsMapper.from_readable(_SETTINGS_DEFAULTS)


def make_v2_node():
    # v25.5.1+ firmware: every settings key, Write Settings V2, 80 byte buffer.
    known = frozenset(SettingsMapper.forward_map.values())
    defaults = {**dict.fromkeys(known, 0), **DEFAULTS}
    return FakeNodeTransport(
        known_keys=known, defaults=defaults, v2_supported=True, request_limit=80
    )


# Multi-byte values so that the full set cannot fit a single request.
ALL_SETTINGS = {
    name: True if SettingsMapper.types[name] is SettingType.BOOL else 70000 + i
    for i, name in enumerate(SettingsMapper.forward_map)
}


def test_update_settings_v2_replace_small():
    transport = make_v2_node()
    client = AVSSClient(transport)

    run(update_settings(client, {"base_sample_rate_hz": 2048}, replace=True))

    assert transport.pending == {**transport.defaults, 0: 2048}
    # Fits a single request, with reset_defaults set.
    assert len(transport.requests) == 1
    assert cbor2.loads(transport.requests[0][1:])[1] is True


def test_update_settings_v2_replace_multiple_requests():
    transport = make_v2_node()
    client = AVSSClient(transport)

    run(update_settings(client, ALL_SETTINGS, replace=True))

    assert transport.pending == SettingsMapper.from_readable(ALL_SETTINGS)
    assert len(transport.requests) > 1
    # Only the first request resets to defaults.
    reset_flags = [cbor2.loads(req[1:])[1] for req in transport.requests]
    assert reset_flags == [True] + [False] * (len(transport.requests) - 1)


def test_update_settings_v2_replace_empty():
    transport = make_v2_node()
    client = AVSSClient(transport)

    run(update_settings(client, {}, replace=True))

    assert transport.pending == transport.defaults
    assert len(transport.requests) == 1
    assert cbor2.loads(transport.requests[0][1:]) == {0: {}, 1: True, 2: False}


def apply_flags(requests):
    return [cbor2.loads(req[1:])[2] for req in requests]


def test_update_settings_v2_replace_apply_rides_single_request():
    transport = make_v2_node()
    client = AVSSClient(transport)

    resp = run(
        update_settings(client, {"base_sample_rate_hz": 2048}, replace=True, apply=True)
    )

    assert transport.applied == {**transport.defaults, 0: 2048}
    # One request carrying the reset, the settings and the apply together.
    assert len(transport.requests) == 1
    assert apply_flags(transport.requests) == [True]
    assert resp == UpdateSettingsResult(applied=True, will_reboot=False)


def test_update_settings_v2_replace_apply_rides_last_request():
    transport = make_v2_node()
    transport.will_reboot = True
    client = AVSSClient(transport)

    resp = run(update_settings(client, ALL_SETTINGS, replace=True, apply=True))

    assert len(transport.requests) > 1
    # Only the final request applies, so intermediate chunks are not applied
    # piecemeal, and no separate Apply Settings request is needed.
    flags = apply_flags(transport.requests)
    assert flags == [False] * (len(flags) - 1) + [True]
    assert all(req[0] == OpCode.WRITE_SETTINGS_V2 for req in transport.requests)
    assert transport.applied == SettingsMapper.from_readable(ALL_SETTINGS)
    assert resp == UpdateSettingsResult(applied=True, will_reboot=True)


def test_update_settings_v2_replace_apply_empty():
    transport = make_v2_node()
    client = AVSSClient(transport)

    run(update_settings(client, {}, replace=True, apply=True))

    assert transport.applied == transport.defaults
    assert cbor2.loads(transport.requests[0][1:]) == {0: {}, 1: True, 2: True}


def test_update_settings_v2_replace_without_apply_stages_only():
    transport = make_v2_node()
    client = AVSSClient(transport)

    resp = run(update_settings(client, ALL_SETTINGS, replace=True))

    assert transport.applied is None
    assert not any(apply_flags(transport.requests))
    assert resp == UpdateSettingsResult(applied=False, will_reboot=None)


def test_update_settings_legacy_replace_apply_uses_separate_request():
    transport = FakeNodeTransport(
        known_keys=V25_1_KEYS,
        defaults=DEFAULTS,
        v2_supported=False,
        request_limit=60,
        will_reboot=True,
    )
    client = AVSSClient(transport)

    resp = run(
        update_settings(client, {"snippet_length": 4096}, replace=True, apply=True)
    )

    # Write Settings has no apply flag, so the apply is its own request and
    # comes last, once every chunk of the replacement is staged.
    assert transport.requests[-1][0] == OpCode.APPLY_SETTINGS
    assert [req[0] for req in transport.requests].count(OpCode.APPLY_SETTINGS) == 1
    assert transport.applied == {
        **transport.defaults,
        **SettingsMapper.from_readable({"snippet_length": 4096}),
    }
    assert resp == UpdateSettingsResult(applied=True, will_reboot=True)


def test_update_settings_legacy_replace_without_apply_stages_only():
    transport = FakeNodeTransport(
        known_keys=V25_1_KEYS,
        defaults=DEFAULTS,
        v2_supported=False,
        request_limit=60,
    )
    client = AVSSClient(transport)

    resp = run(update_settings(client, {"snippet_length": 4096}, replace=True))

    assert transport.applied is None
    assert not any(req[0] == OpCode.APPLY_SETTINGS for req in transport.requests)
    assert resp == UpdateSettingsResult(applied=False, will_reboot=None)


def test_update_settings_legacy_replace_apply_without_response_detail():
    # Pre-v24.6.0 firmware answers Apply Settings with a bare OK.
    transport = FakeNodeTransport(
        known_keys=V24_9_KEYS,
        defaults=DEFAULTS,
        v2_supported=False,
        request_limit=60,
        apply_detail=False,
    )
    client = AVSSClient(transport)

    resp = run(
        update_settings(client, {"snippet_length": 4096}, replace=True, apply=True)
    )

    assert transport.applied is not None
    assert resp == UpdateSettingsResult(applied=True, will_reboot=None)


@pytest.mark.parametrize("known_keys", [V24_9_KEYS, V25_1_KEYS])
def test_update_settings_legacy_replace(known_keys):
    # Firmware without Write Settings V2: a 60 byte buffer, and settings not
    # passed by the caller are reset by explicitly writing their defaults.
    transport = FakeNodeTransport(
        known_keys=known_keys,
        defaults=DEFAULTS,
        v2_supported=False,
        request_limit=60,
    )
    client = AVSSClient(transport)

    passed = {"snippet_length": 4096, "wom_threshold_g": 0.1}
    run(update_settings(client, passed, replace=True))

    assert transport.pending == {
        **transport.defaults,
        **SettingsMapper.from_readable(passed),
    }
    # The rejected V2 probe, then the defaults split over several requests.
    assert transport.requests[0][0] == OpCode.WRITE_SETTINGS_V2
    write_requests = transport.requests[1:]
    assert len(write_requests) > 1
    assert all(req[0] == OpCode.WRITE_SETTINGS for req in write_requests)
    # Together the requests carry the full default set plus the caller's
    # settings, caller's values winning.
    written = {}
    for req in write_requests:
        written.update(cbor2.loads(req[1:]))
    assert written == {**DEFAULTS, **SettingsMapper.from_readable(passed)}


def test_update_settings_legacy_replace_caller_settings_override_defaults():
    transport = FakeNodeTransport(
        known_keys=V24_9_KEYS,
        defaults=DEFAULTS,
        v2_supported=False,
        request_limit=60,
    )
    client = AVSSClient(transport)

    run(update_settings(client, {"base_sample_rate_hz": 512}, replace=True))

    assert transport.pending[0] == 512


@pytest.mark.parametrize("known_keys", [V24_9_KEYS, V25_1_KEYS])
def test_update_settings_v2_probe_fits_legacy_request_buffer(known_keys):
    # The first Write Settings V2 request doubles as the probe for V2 support,
    # so it must fit the 60 byte buffer of firmware that has neither V2 nor a
    # length check on the request, however many settings are passed.
    transport = FakeNodeTransport(
        known_keys=known_keys,
        defaults=DEFAULTS,
        v2_supported=False,
        request_limit=60,
    )
    client = AVSSClient(transport)

    run(update_settings(client, ALL_SETTINGS, replace=True))

    assert transport.requests[0][0] == OpCode.WRITE_SETTINGS_V2
    mapped = SettingsMapper.from_readable(ALL_SETTINGS)
    assert transport.pending == {k: mapped[k] for k in known_keys}


def test_update_settings_v2_merge_leaves_other_settings_alone():
    transport = make_v2_node()
    client = AVSSClient(transport)

    resp = run(update_settings(client, {"base_sample_rate_hz": 2048}, replace=False))

    # Without replace only the given setting is touched; the rest keep
    # whatever the node had, rather than falling back to defaults.
    assert transport.pending == {
        **dict.fromkeys(transport.known_keys, SENTINEL),
        0: 2048,
    }
    assert len(transport.requests) == 1
    assert cbor2.loads(transport.requests[0][1:])[1] is False
    assert resp == UpdateSettingsResult(applied=False, will_reboot=None)


def test_update_settings_v2_merge_multiple_requests():
    transport = make_v2_node()
    client = AVSSClient(transport)

    run(update_settings(client, ALL_SETTINGS, replace=False))

    # The merge is chunked just like the reset, and no request resets.
    assert len(transport.requests) > 1
    assert not any(cbor2.loads(req[1:])[1] for req in transport.requests)
    assert transport.pending == SettingsMapper.from_readable(ALL_SETTINGS)


def test_update_settings_v2_merge_apply_rides_last_request():
    transport = make_v2_node()
    client = AVSSClient(transport)

    resp = run(update_settings(client, ALL_SETTINGS, replace=False, apply=True))

    flags = apply_flags(transport.requests)
    assert flags == [False] * (len(flags) - 1) + [True]
    assert transport.applied == SettingsMapper.from_readable(ALL_SETTINGS)
    assert resp == UpdateSettingsResult(applied=True, will_reboot=False)


def test_update_settings_legacy_merge_writes_no_defaults():
    transport = FakeNodeTransport(
        known_keys=V25_1_KEYS,
        defaults=DEFAULTS,
        v2_supported=False,
        request_limit=60,
    )
    client = AVSSClient(transport)

    passed = {"snippet_length": 4096, "wom_threshold_g": 0.1}
    run(update_settings(client, passed, replace=False))

    # The rejected V2 probe, then only the caller's settings: a merge must
    # not drag in the default of every other setting.
    assert transport.requests[0][0] == OpCode.WRITE_SETTINGS_V2
    written = {}
    for req in transport.requests[1:]:
        assert req[0] == OpCode.WRITE_SETTINGS
        written.update(cbor2.loads(req[1:]))
    assert written == SettingsMapper.from_readable(passed)


def test_update_settings_nothing_requested_issues_no_requests():
    transport = make_v2_node()
    client = AVSSClient(transport)

    resp = run(update_settings(client, {}, replace=False))

    # No settings, no reset and no apply: nothing to ask the node for.
    assert transport.requests == []
    assert resp == UpdateSettingsResult(applied=False, will_reboot=None)


def test_update_settings_apply_only():
    transport = make_v2_node()
    client = AVSSClient(transport)

    resp = run(update_settings(client, {}, replace=False, apply=True))

    # Applying whatever is already staged still takes one request.
    assert cbor2.loads(transport.requests[0][1:]) == {0: {}, 1: False, 2: True}
    assert resp == UpdateSettingsResult(applied=True, will_reboot=False)


def test_chunk_settings_respects_limit():
    settings = SettingsMapper.from_readable(ALL_SETTINGS)
    size = _write_settings_size
    chunks = _chunk_settings(settings, size, 60)

    assert len(chunks) > 1
    assert all(size(chunk) <= 60 for chunk in chunks)
    merged = {}
    for chunk in chunks:
        merged.update(chunk)
    assert merged == settings


def test_chunk_settings_first_limit_caps_first_chunk_only():
    settings = SettingsMapper.from_readable(ALL_SETTINGS)
    size = _write_settings_size
    chunks = _chunk_settings(settings, size, 80, first_limit=60)

    assert size(chunks[0]) <= 60
    assert all(size(chunk) <= 80 for chunk in chunks)
    # The chunks after the first are not held to the tighter first limit.
    assert any(size(chunk) > 60 for chunk in chunks[1:])
    merged = {}
    for chunk in chunks:
        merged.update(chunk)
    assert merged == settings


def test_chunk_settings_oversized_setting():
    with pytest.raises(ValueError):
        _chunk_settings({0: b"x" * 100}, _write_settings_size, 60)
