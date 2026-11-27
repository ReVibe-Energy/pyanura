# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0] - 2026-11-27

### Upgrading from 1.x
- Import `pyanura` instead of `anura`. Loggers are now named `pyanura.*`.
- Use `AVSSClient` with `BleakAVSSTransport` or `ProxyAVSSTransport` in
  place of `BleakAVSSClient` and `ProxyAVSSClient`.
- Give every `AVSSClient.report_*` call a count. Use `UNLIMITED` where you
  passed `None`.
- Catch `AVSSConnectionError` where you caught `TimeoutError` from an
  `AVSSClient` request, and reconnect.
- Transceiver requests now time out after 5 seconds. Pass `timeout` to
  `TransceiverClient.request()` for calls that need longer.
- In the CLI, use `avss update-settings --merge` for `avss write-settings`,
  `avss update-settings` for `write-settings --reset-defaults`, and
  `avss reset-settings` to only reset.

### Added
- `pyanura.dfu` for reading firmware bundles (`read_bundle`) and checking
  them against the firmware a device runs (`unmet_dependencies`).
- Firmware bundles are accepted by the CLI `avss upgrade` and
  `transceiver upgrade` commands.
- `pyanura.avss.procedures.update_settings`, which replaces or merges a
  node's settings and works with settings of any size.
- CLI `avss update-settings` and `avss reset-settings` commands.
- `AVSSClient.get_device_info` and the CLI `avss get-device-info` command.
- `firmware_pn` and `app_confirmed` in the firmware info of nodes and
  transceivers.
- `device_pn` in the device info of nodes and transceivers.
- `AVSSClient.report_stats`, `StatsReport` and the CLI `avss stats` command.
- `CaptureReport.reasons` and `CaptureReason`.
- `ReportType.THROUGHPUT`.
- AVSS transports can be used with `async with`.
- `timeout` argument on `TransceiverClient.request()` and `avss_request()`.
  The latter needs transceiver firmware support, see
  `supports_avss_request_timeout`.
- `APIErrorCode.TIMEOUT` and `APIErrorCode.BUSY`.
- Debian package (`python3-pyanura`) and source distributions.

### Changed
- The import package is renamed from `anura` to `pyanura`, to match the
  distribution name. Loggers are renamed the same way.
- `AVSSClient.report_*` require a count, which is a number or `UNLIMITED`.
  `None` is no longer accepted.
- A request the node does not answer raises `AVSSConnectionError` instead
  of `TimeoutError`, and the transport is closed.
- `AVSSTransport.control_point_request()` takes a `timeout`, which the
  transport enforces.
- Firmware transfers fail after 60 seconds without progress
  (`PROGRAM_PROGRESS_TIMEOUT`).
- `ProxyAVSSTransport` raises AVSS exceptions instead of transceiver
  exceptions.
- `ProxyAVSSTransport.open()` fails instead of waiting forever for a node
  that is not assigned to the transceiver, is busy or does not respond.
- `TransceiverClient` requests time out after 5 seconds by default. The
  connection is closed when a request times out.
- `TransceiverClient.avss_program_write()` raises `TimeoutError` when the
  write could not be sent in time. The write can be retried.
- No keepalive pings are sent over USB.
- `SettingsMapper.from_readable` converts values to the type the firmware
  expects and rejects values of the wrong type.
- `marshal()` omits optional fields that are `None` from the input, and
  rejects `None` in non-optional fields.
- `unmarshal()` handles unions more broadly than the earlier `X | None`.

### Deprecated
- The `anura` import name. It will be removed in 3.0.

### Removed
- `BleakAVSSClient` and `ProxyAVSSClient`.
- The `active` argument of `AVSSClient.report_health`.
- `PROGRAM_STALL_LIMIT`.
- CLI `avss write-settings` command.

### Fixed
- `AVSSClient.write_settings_v2` failed with current node firmware.
- Transceiver upgrades could stall with fielded transceiver firmware.
- `BleakAVSSTransport` could return the response to an earlier, abandoned
  request.
- `ProxyAVSSTransport.open()` left a background task running when it failed.
- CBOR payloads with trailing bytes are rejected.
- `AVSSClient` raises `AVSSProtocolError` for a payload it cannot decode.
- The CLI throughput commands could measure the wrong report.

## [1.1.0] - 2026-07-04

### Added
- Support for the AVSS windowed DFU transfer (Prepare Upgrade V2), adding
  flow control and resumption of interrupted transfers:
  `AVSSClient.prepare_upgrade_v2` and `AVSSClient.program_transfer_windowed`,
  plus the `PrepareUpgradeV2Args`/`PrepareUpgradeV2Response` models and
  `AVSSProgramTransferError`. The existing `prepare_upgrade`/`program_transfer`
  pair is unchanged.
- `anura.avss.procedures`: high-level procedures composing the low-level
  client methods into complete transactions. The first is `upload_firmware`,
  which prepares and uploads an image using the windowed transfer and falls
  back to the legacy transfer on node firmware without support; `pyanura-cli`
  now uses it for `avss upgrade`.

### Changed
- The firmware-transfer loops (`dfu_write_image`, `program_transfer`) no longer emit per-chunk `INFO` log records. Callers wanting progress should pass the new `progress` callback instead.

### Fixed
- `AVSSControlPointError.from_response` raised `TypeError` instead of the
  proper AVSS exception for any device error response on Python 3.11, where
  `int in IntEnum` membership tests are not supported.
- The legacy AVSS firmware transfer no longer reports completion while the
  node is still missing data, which made the subsequent Apply Upgrade fail.
  The transfer now confirms completion by requiring NACK silence after the
  final chunk and re-sending it as a probe, recovering both late NACKs and a
  silently dropped final chunk.

## [1.0.0] - 2026-07-04

### Added
- `SnippetReport` and `CaptureReport` now decode the timing fields added in firmware v26.4.0 (`duration`, `start_time_monotonic`, `duration_monotonic`, `transmission_offset`); all are optional, so reports from older firmware still decode.
- Optional `progress` callback on `TransceiverClient.dfu_write_image` and `AVSSClient.program_transfer`, invoked with the cumulative byte count after each chunk so embedders can drive upload-progress UI without re-implementing the transfer loop.
- `pyanura-cli` now shows a progress bar while uploading firmware images (`transceiver upgrade` and `avss upgrade`), driven by the new `progress` callbacks.
- CI: a pyright type-check job that runs both with and without the optional `ble`/`usb` extras, keeping the library type-clean in either configuration.

### Changed
- Reworked CBOR (un)marshalling to carry wire keys via `typing.Annotated` (`CborKey`) plus a per-type codec registry, replacing the `cbor_field` helper. Protocol model dataclasses now keep their real field types, so constructing and consuming them is fully type-checked. The on-the-wire CBOR format is unchanged.

### Removed
- The internal `cbor_field` helper, superseded by `Annotated[..., CborKey(n)]` and `register_codec`.

### Fixed
- `DfuWriteArgs.data` is now correctly typed as `bytes` (it was previously annotated `int`).
- pyanura now type-checks cleanly under pyright; corrected latent `Optional`-narrowing and return-type annotations in the USB and TCP transports.

## [1.0.0a4] - 2026-05-19

### Added
- `py.typed` marker so downstream type checkers see pyanura's annotations.
- Helpful error messages when the optional `bleak` (`pip install pyanura[ble]`) or `pyusb` (`pip install pyanura[usb]`) dependencies are missing.
- `control_point_timeout` attribute on `AVSSClient`.
- `pyanura-cli` package metadata and a short README so the published listing isn't blank.
- CI: `ruff check` lint job covering the whole workspace.
- CI: publish workflow now pushes tagged releases (`v*`) to the public package index in addition to the dev index.

### Changed
- Filled in `pyproject.toml` metadata (description, authors, readme, keywords, classifiers, project URLs) for both `pyanura` and `pyanura-cli`.
- README rewritten to use `uv` for development and to install released wheels from the public package index.
- Examples are now self-contained `uv` projects that depend on `pyanura` as an editable path source.
- `BleakError` from the underlying `bleak` library is now wrapped in pyanura's AVSS exception hierarchy.
- `TransceiverClient` now unmarshals API errors using the same path as successful responses, giving callers consistent error objects.
- Cleaner error messages in `AVSSOpCodeUnsupportedError` and `AVSSBadArgumentError`.
- Enabled an extended set of ruff lint rules (`I`, `B`, `UP`, `RUF`) and fixed the resulting findings.

### Fixed
- `ProxyAVSSTransport._wait_available` no longer raises `NameError` when the transceiver returns an unexpected API error.
- `BleakAVSSTransport` correctly invokes the registered `_closed_callback`.
- `TransceiverClient` now handles "method not found" responses cleanly instead of treating them as protocol errors.

## [1.0.0a3] - 2026-04-09

### Added
- `VERSION` file as the single source of truth for the package version.
- CI: Publish workflow runs on `v*` tags and stamps dev builds with a snapshot version.

### Changed
- Switched the build backend from setuptools to hatchling.

## [1.0.0a2] - 2026-04-07

### Fixed
- Removed committed merge-conflict markers from `proxy_avss_client.py`.
- `BleakAVSSTransport.set_closed_callback` referenced the wrong variable.

## [1.0.0a1] - 2026-04-01

### Added
- New transport-based architecture: `BleakAVSSTransport` and `ProxyAVSSTransport` decouple the AVSS protocol from the underlying connection. Use them with `AVSSClient` directly.
- `pyanura-cli` as a separate workspace package shipping the `anura` command-line tool.
- Apache 2.0 license.
- CI: Publish workflow targeting a public dev package index.
- `examples/forwarder` and `examples/collect_files` are now self-contained `uv` projects with their own `pyproject.toml`.
- Re-exported common types from `anura.avss` and `anura.transceiver` package roots.

### Changed
- Repository converted to the `src/` layout; the import package `anura` now lives under `src/anura/`.
- Overhauled error handling across the transceiver and AVSS layers.
- `Transport.send` takes only a positional argument.
- Fixed typing for `AVSSClient.report` and various `marshalling` warnings.

### Deprecated
- `BleakAVSSClient` and `ProxyAVSSClient` — use `BleakAVSSTransport` / `ProxyAVSSTransport` with `AVSSClient` instead. The old classes remain as thin wrappers and emit a `DeprecationWarning` on instantiation.

## [0.14.0] - 2026-03-31

### Added
- "Trigger capture" command on the AVSS client.

### Fixed
- `TransceiverClient` now awaits the connection task in `__aexit__` to ensure the connection is fully torn down before the context manager returns.

[Unreleased]: https://github.com/ReVibe-Energy/pyanura/compare/v2.0...HEAD
[2.0.0]: https://github.com/ReVibe-Energy/pyanura/compare/v1.1...v2.0
[1.1.0]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0...v1.1
[1.0.0]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a4...v1.0
[1.0.0a4]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a3...v1.0a4
[1.0.0a3]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a2...v1.0a3
[1.0.0a2]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a1...v1.0a2
[1.0.0a1]: https://github.com/ReVibe-Energy/pyanura/compare/v0.14.0...v1.0a1
[0.14.0]: https://github.com/ReVibe-Energy/pyanura/releases/tag/v0.14.0
