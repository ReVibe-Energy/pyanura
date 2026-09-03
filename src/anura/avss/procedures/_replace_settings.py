"""Settings replacement procedure."""

import logging
from collections.abc import Callable
from typing import Any

import cbor2

from anura.marshalling import marshal

from ..client import AVSSClient
from ..exceptions import AVSSOpCodeUnsupportedError
from ..models import WriteSettingsV2Args
from ..settings import SettingsMapper

logger = logging.getLogger(__name__)

# A Control Point request (one opcode byte plus the CBOR-encoded argument)
# must fit the node's request buffer: 60 bytes on firmware without Write
# Settings V2 support, 80 bytes on all firmware with it (the buffer grew to
# 80 in v25.1.0, before Write Settings V2 was introduced in v25.5.1).
_REQUEST_LIMIT = 60
_REQUEST_LIMIT_V2 = 80

# Factory defaults of every setting found on firmware without built-in
# settings reset (Write Settings V2 and Reset Settings both arrived in
# v25.5.1). Keys 0-9 are the complete set as of v24.9.0; v25.1.0 added the
# capture and motion-start event settings. The defaults are identical across
# all of those releases, and firmware skips settings it does not know, so
# writing the full set resets any pre-v25.5.1 node to a known state.
_SETTINGS_DEFAULTS: dict[str, Any] = {
    "base_sample_rate_hz": 1024,
    "snippet_interval_ms": 10000,
    "snippet_length": 1024,
    "health_interval_ms": 60000,
    "base_axis_enable": 0b111,
    "motion_threshold_rms_g": 0.05,
    "motion_standby_delay_ms": 1000,
    "wom_sample_rate_hz": 50,
    "wom_threshold_g": 0.05,
    "snippet_mode": 1,  # interval
    # Added in v25.1.0:
    "capture_mode": 0,  # disabled
    "capture_buffer_length": 5120,
    "events_motion_start_enable": False,
    "events_motion_start_capture": False,
    "events_motion_start_capture_duration_ms": 0,
}


def _write_settings_size(settings: dict[int, Any]) -> int:
    return 1 + len(cbor2.dumps(marshal(settings)))


def _write_settings_v2_size(settings: dict[int, Any]) -> int:
    # reset_defaults and apply encode as one byte each regardless of value.
    arg = WriteSettingsV2Args(settings=settings, reset_defaults=True, apply=False)
    return 1 + len(cbor2.dumps(marshal(arg)))


def _chunk_settings(
    settings: dict[int, Any],
    request_size: Callable[[dict[int, Any]], int],
    limit: int,
) -> list[dict[int, Any]]:
    """Split settings into chunks whose encoded requests stay within limit."""
    chunks: list[dict[int, Any]] = []
    chunk: dict[int, Any] = {}
    for key, value in settings.items():
        grown = {**chunk, key: value}
        if chunk and request_size(grown) > limit:
            chunks.append(chunk)
            grown = {key: value}
        if request_size(grown) > limit:
            raise ValueError(
                f"Setting {key} alone exceeds the control point request limit"
            )
        chunk = grown
    if chunk:
        chunks.append(chunk)
    return chunks


async def replace_settings(client: AVSSClient, settings: dict) -> None:
    """Replace the node's settings with exactly the given set.

    Writes the given settings and resets every other setting to its default,
    leaving the node in a known state regardless of what was written to it
    before. The write is split over multiple Control Point requests when the
    settings do not fit within a single request.

    On firmware with Write Settings V2 (v25.5.1+) its built-in
    reset-to-defaults is used. Older firmware has no reset, so the default
    of every setting it may know is written explicitly, overlaid with the
    given settings.

    Like ``AVSSClient.write_settings``, this only stages pending settings;
    applying them is left to the caller (``AVSSClient.apply_settings``).

    Args:
        client:   The AVSS client to operate on.
        settings: Settings to write, keyed like ``AVSSClient.write_settings``
                  (readable names or raw integer keys).
    """
    mapped = SettingsMapper.from_readable(settings)
    try:
        await _replace_settings_v2(client, mapped)
    except AVSSOpCodeUnsupportedError:
        logger.info(
            "Write Settings V2 not supported by node, writing explicit defaults"
        )
        await _replace_settings_legacy(client, mapped)


async def _replace_settings_v2(client: AVSSClient, mapped: dict[int, Any]) -> None:
    chunks = _chunk_settings(mapped, _write_settings_v2_size, _REQUEST_LIMIT_V2)
    # The first request resets every setting to its default before staging
    # the settings it carries; the following requests amend the result.
    reset_defaults = True
    for chunk in chunks or [{}]:
        await client.write_settings_v2(
            chunk, reset_defaults=reset_defaults, apply=False
        )
        reset_defaults = False


async def _replace_settings_legacy(client: AVSSClient, mapped: dict[int, Any]) -> None:
    augmented = {**SettingsMapper.from_readable(_SETTINGS_DEFAULTS), **mapped}
    for chunk in _chunk_settings(augmented, _write_settings_size, _REQUEST_LIMIT):
        await client.write_settings(chunk)
