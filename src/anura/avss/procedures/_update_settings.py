"""Settings update procedure."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cbor2

from anura.marshalling import marshal

from ..client import AVSSClient
from ..exceptions import AVSSOpCodeUnsupportedError
from ..models import WriteSettingsV2Args
from ..settings import SettingsMapper

logger = logging.getLogger(__name__)

# A Control Point request (one opcode byte plus the CBOR-encoded argument)
# must fit the node's request buffer, and firmware before v25.5.1 copies the
# request into it without checking the length first. The buffer is 60 bytes up
# to v24.9.0 and 80 bytes in v25.1.0 .. v25.5.0, so 60 bytes is the only size
# safe on any firmware without Write Settings V2. Firmware with it (v25.5.1
# and later) accepts far larger requests; 80 bytes is a conservative cap.
_REQUEST_LIMIT = 60
_REQUEST_LIMIT_V2 = 80

# Factory defaults of every setting found on firmware without built-in settings
# reset (that capability arrived in v25.5.1). The defaults are identical across
# all of those releases, and firmware skips settings it does not know, so
# writing the full set resets any pre-v25.5.1 node to a known state.
_SETTINGS_DEFAULTS: dict[str, Any] = {
    # Complete set up to 24.9.0:
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
    first_limit: int | None = None,
) -> list[dict[int, Any]]:
    """Split settings into chunks whose encoded requests stay within limit.

    ``first_limit``, when given, caps the first chunk instead of ``limit``.
    """
    chunks: list[dict[int, Any]] = []
    chunk: dict[int, Any] = {}
    chunk_limit = limit if first_limit is None else first_limit
    for key, value in settings.items():
        grown = {**chunk, key: value}
        if chunk and request_size(grown) > chunk_limit:
            chunks.append(chunk)
            chunk_limit = limit
            grown = {key: value}
        if request_size(grown) > chunk_limit:
            raise ValueError(
                f"Setting {key} alone exceeds the control point request limit"
            )
        chunk = grown
    if chunk:
        chunks.append(chunk)
    return chunks


@dataclass
class UpdateSettingsResult:
    """Outcome of :func:`update_settings`.

    Attributes:
        applied:     Whether the settings were applied and persisted, that
                     is, whether ``apply`` was requested.
        will_reboot: Whether the node reboots to take the settings into use.
                     None when nothing was applied, and when the node's
                     firmware does not report it (pre-v24.6.0).
    """

    applied: bool
    will_reboot: bool | None = None


async def update_settings(
    client: AVSSClient,
    settings: dict,
    *,
    replace: bool = True,
    apply: bool = False,
) -> UpdateSettingsResult:
    """Write settings to the node.

    By default the node is left with exactly the given settings, every
    setting not given reset to its default, regardless of what was written
    to the node before; passing no settings at all therefore resets the node.
    With ``replace=False`` the given settings are merged into the node's
    settings instead, leaving every setting not given as it was.

    Without ``apply`` this only stages pending settings, like
    ``AVSSClient.write_settings``, and applying them is left to the caller
    (``AVSSClient.apply_settings``). With ``apply`` they are applied and
    persisted before returning.

    Any number of settings may be given, on any node firmware.

    Args:
        client:   The AVSS client to operate on.
        settings: Settings to write, keyed like ``AVSSClient.write_settings``
                  (readable names or raw integer keys).
        replace:  Leave the node with exactly the given settings, resetting
                  every setting not given to its default. Pass False to merge
                  the given settings into the node's settings instead.
        apply:    Apply and persist the settings once they are staged.

    Returns:
        An :class:`UpdateSettingsResult` describing whether the settings were
        applied and whether the node reboots to take them into use.
    """
    mapped = SettingsMapper.from_readable(settings)
    try:
        return await _update_settings_v2(client, mapped, replace, apply)
    except AVSSOpCodeUnsupportedError:
        logger.debug("Write Settings V2 not supported by node, using Write Settings")
        return await _update_settings_legacy(client, mapped, replace, apply)


async def _update_settings_v2(
    client: AVSSClient,
    mapped: dict[int, Any],
    replace: bool,
    apply: bool,
) -> UpdateSettingsResult:
    # The first request doubles as the probe for Write Settings V2 support, so
    # it may land on firmware without it, whose request buffer is as small as
    # 60 bytes and is filled without checking the length. Keep that one within
    # the smallest buffer; once it has been answered the node is known to
    # accept the larger requests.
    chunks = _chunk_settings(
        mapped,
        _write_settings_v2_size,
        _REQUEST_LIMIT_V2,
        first_limit=_REQUEST_LIMIT,
    )
    if not chunks and (replace or apply):
        # Nothing to write, but a request with no settings still carries the
        # reset and the apply.
        chunks = [{}]
    # Only the first request resets to defaults, so the ones after it amend
    # that result rather than starting over. The apply rides the last one, so
    # no separate Apply Settings request is needed.
    resp = None
    for index, chunk in enumerate(chunks):
        resp = await client.write_settings_v2(
            chunk,
            reset_defaults=replace and index == 0,
            apply=apply and index == len(chunks) - 1,
        )
    if not apply:
        return UpdateSettingsResult(applied=False)
    assert resp is not None  # apply forces at least one request
    return UpdateSettingsResult(applied=True, will_reboot=resp.will_reboot)


async def _update_settings_legacy(
    client: AVSSClient,
    mapped: dict[int, Any],
    replace: bool,
    apply: bool,
) -> UpdateSettingsResult:
    if replace:
        # This firmware has no built-in reset, so the default of every
        # setting it may know is written explicitly, the caller's settings
        # overlaid on top.
        mapped = {**SettingsMapper.from_readable(_SETTINGS_DEFAULTS), **mapped}
    for chunk in _chunk_settings(mapped, _write_settings_size, _REQUEST_LIMIT):
        await client.write_settings(chunk)
    if not apply:
        return UpdateSettingsResult(applied=False)
    # Write Settings has no apply flag, so this path needs its own request.
    resp = await client.apply_settings(persist=True)
    # Firmware before v24.6.0 answers with a bare OK, reporting no detail.
    return UpdateSettingsResult(
        applied=True, will_reboot=resp.will_reboot if resp is not None else None
    )
