"""Tests for the settings replacement procedure."""

import asyncio

import cbor2
import pytest

from anura.avss.client import AVSSClient
from anura.avss.procedures import replace_settings
from anura.avss.procedures._replace_settings import (
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
    """

    def __init__(self, *, known_keys, defaults, v2_supported, request_limit):
        self.known_keys = known_keys
        self.defaults = {k: v for k, v in defaults.items() if k in known_keys}
        self.v2_supported = v2_supported
        self.request_limit = request_limit
        self.pending = {k: SENTINEL for k in known_keys}
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

    async def control_point_request(self, req):
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
            return bytes([OpCode.WRITE_SETTINGS_V2_RESPONSE]) + cbor2.dumps({1: False})

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


def test_replace_settings_v2_small():
    transport = make_v2_node()
    client = AVSSClient(transport)

    run(replace_settings(client, {"base_sample_rate_hz": 2048}))

    assert transport.pending == {**transport.defaults, 0: 2048}
    # Fits a single request, with reset_defaults set.
    assert len(transport.requests) == 1
    assert cbor2.loads(transport.requests[0][1:])[1] is True


def test_replace_settings_v2_multiple_requests():
    transport = make_v2_node()
    client = AVSSClient(transport)

    run(replace_settings(client, ALL_SETTINGS))

    assert transport.pending == SettingsMapper.from_readable(ALL_SETTINGS)
    assert len(transport.requests) > 1
    # Only the first request resets to defaults.
    reset_flags = [cbor2.loads(req[1:])[1] for req in transport.requests]
    assert reset_flags == [True] + [False] * (len(transport.requests) - 1)


def test_replace_settings_v2_empty():
    transport = make_v2_node()
    client = AVSSClient(transport)

    run(replace_settings(client, {}))

    assert transport.pending == transport.defaults
    assert len(transport.requests) == 1
    assert cbor2.loads(transport.requests[0][1:]) == {0: {}, 1: True, 2: False}


@pytest.mark.parametrize("known_keys", [V24_9_KEYS, V25_1_KEYS])
def test_replace_settings_legacy(known_keys):
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
    run(replace_settings(client, passed))

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


def test_replace_settings_legacy_caller_settings_override_defaults():
    transport = FakeNodeTransport(
        known_keys=V24_9_KEYS,
        defaults=DEFAULTS,
        v2_supported=False,
        request_limit=60,
    )
    client = AVSSClient(transport)

    run(replace_settings(client, {"base_sample_rate_hz": 512}))

    assert transport.pending[0] == 512


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


def test_chunk_settings_oversized_setting():
    with pytest.raises(ValueError):
        _chunk_settings({0: b"x" * 100}, _write_settings_size, 60)
