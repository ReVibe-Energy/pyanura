# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Upgrading from 1.x
- Import `pyanura` instead of `anura`. The old name keeps working through
  2.x with a `DeprecationWarning`. Logger names changed with it, from
  `anura.*` to `pyanura.*`, so update any logging configuration that names
  them.
- Replace `BleakAVSSClient` and `ProxyAVSSClient` with a transport and an
  `AVSSClient`:

  ```python
  async with ProxyAVSSTransport(transceiver, address) as transport:
      node = AVSSClient(transport)
  ```

- Pass a count to every `AVSSClient.report_*` call, and `UNLIMITED` where
  you passed `None` or, to `report_health`, nothing at all.
- Catch `AVSSConnectionError` where you caught `TimeoutError` from an
  `AVSSClient` request. A node that does not answer is now treated as a lost
  connection: the transport is closed and must be reopened.
- Expect `TransceiverClient` requests to fail after 5 seconds without an
  answer, closing the connection. Pass `timeout` to `request()` for a call
  that needs longer.
- If you marshal dataclasses of your own, note that `marshal()` leaves out
  optional fields that are None and rejects None anywhere else.
- In scripts, replace `pyanura-cli avss write-settings --file F` with
  `avss update-settings --merge --file F`, `write-settings --reset-defaults
  --file F` with `avss update-settings --file F`, and a bare
  `write-settings --reset-defaults` with `avss reset-settings`.

### Added
- `pyanura.dfu`: reading and checking ANURA firmware bundles.
  - `read_bundle` reads a bundle into a `Bundle`: its images, and its
    `meta.json` manifest as written, as a type per manifest version
    (`ManifestV1`). A bundle that is malformed, oversized (a manifest above
    32 KiB or an image above 1 MiB decompressed) or cannot be installed app
    image first is rejected.
  - `Version` parses the versions a manifest holds, and `Version.matches`
    tests one against a `*_version_match` specification.
  - `running_images` reads the images a device runs (`InstalledImage`) from
    a firmware info response. `ManifestV1Image.matches` tells whether an
    image is already running, and `unmet_dependencies` which of a bundle's
    dependencies the device does not meet.
- The CLI `avss upgrade` and `transceiver upgrade` commands accept a firmware
  bundle as `--file` in addition to a raw image. Images the device already
  runs are skipped, and each image's dependencies are checked before it is
  installed.
- `pyanura.avss.procedures.update_settings`: writes settings to a node, split
  over several Control Point requests if required. The node is left with
  exactly the given settings, or with `replace=False` they are merged into
  the settings it has. Works on firmware with and without a built-in reset.
- The CLI `avss update-settings` and `avss reset-settings` commands. The
  former updates a node's settings from a settings file of any size, on any
  firmware version, resetting the settings the file does not name unless
  given `--merge`. The latter resets every setting to its default.
- `AVSSClient.get_device_info` (opcode 24) and the CLI
  `avss get-device-info`.
- `firmware_pn` in the firmware info of nodes (`GetFirmwareInfoResponse`)
  and transceivers (`GetFirmwareInfoResult`), and `device_pn` in the
  transceiver's `GetDeviceInfoResult`. The CLI shows them.
- `app_confirmed` on `GetFirmwareInfoResponse` and `GetFirmwareInfoResult`:
  whether the running app image is confirmed. The CLI `avss
  get-firmware-info` and `transceiver get-firmware-info` show it, and now
  print the same formatted output.
- `AVSSClient.report_stats` and `StatsReport` (report type 9), and the CLI
  `avss stats` command, printing every stats group as a line or as JSON.
- `CaptureReport.reasons` (key 9), with its bits defined by
  `pyanura.avss.CaptureReason`.
- `ReportType.THROUGHPUT` (report type 7), sent by the Test Throughput
  command.
- `AVSSTransport` is an async context manager, opening the transport on
  entry and closing it on exit.
- `APIErrorCode.TIMEOUT`, `APIErrorCode.BUSY` and
  `AVSSRequestArgs.timeout_ms` in the transceiver API, and
  `TransceiverClient.supports_avss_request_timeout`, telling whether the
  transceiver firmware can bound a node request.
- A Debian package, `python3-pyanura`, and source distributions of
  `pyanura` and `pyanura-cli`, published alongside the wheels.

### Changed
- The import package is now `pyanura` (previously `anura`), matching the
  distribution name and avoiding a clash with the unrelated `anura` package
  on PyPI. Loggers are renamed accordingly, from `anura.*` to `pyanura.*`.
- `AVSSClient.report_*` take a count of reports or `pyanura.avss.UNLIMITED`,
  no longer `None`, and `report_health` no longer defaults to unlimited
  reports. The model fields are typed `int | Unlimited`.
- `AVSSClient` control point requests raise `AVSSConnectionError`, not
  `TimeoutError`, when the device does not answer. The transport closes
  itself, since a late response would be taken for the answer to the next
  request.
- `AVSSTransport.control_point_request()` takes a `timeout` and is
  responsible for enforcing it, raising `TimeoutError` and closing itself
  when it expires. `program_write()` may raise `TimeoutError` for a write it
  could not send in time; the connection is intact and `AVSSClient` retries
  the write. Custom transports must follow suit.
- `AVSSClient` program transfers fail once `PROGRAM_PROGRESS_TIMEOUT` (60 s)
  passes without progress. This replaces `PROGRAM_STALL_LIMIT` and also
  bounds the legacy transfer, which could go on forever against a node that
  kept rejecting writes.
- `ProxyAVSSTransport` raises `AVSSConnectionError` when the node is
  unavailable or the transceiver connection broke, and `AVSSTransportError`
  for other request failures, instead of leaking transceiver exceptions.
  `AVSSClient` passes `AVSSTransportError` through unwrapped.
- `ProxyAVSSTransport.open()` fails with `AVSSConnectionError` instead of
  waiting indefinitely when the transceiver is not assigned to the node,
  reports the node's control point as busy, or the node is connected but
  does not respond.
- `TransceiverClient` requests fail with `TransceiverConnectionError`, and
  the connection is closed, if the transceiver does not answer within 5
  seconds, instead of waiting indefinitely. `request()` takes a `timeout`
  to adjust or disable (`None`) the limit per call.
- A `TransceiverClient` request runs to completion, or to its own timeout,
  even if its caller stops waiting for it.
- `TransceiverClient.avss_request()` takes a `timeout` for the node's
  response and raises `TimeoutError` when it expires. This requires
  transceiver firmware support (`supports_avss_request_timeout`).
- `TransceiverClient.avss_program_write()` raises `TimeoutError` when the
  transceiver reports that the write timed out before it was sent. The node
  is still connected and the write may be retried.
- The USB transceiver transport no longer sends keepalive pings.
- `SettingsMapper.from_readable` converts each known setting to the type
  the firmware expects, and raises for a value that does not fit, such as a
  fractional number for an integer setting.
- `marshal()` omits optional dataclass fields (`X | None`) whose value is
  None instead of encoding them as null, and raises `TypeError` for None
  anywhere else.
- `unmarshal()` decodes unions of any member types, not only `X | None`, and
  type-checks when given a union or a parameterised `list`/`dict`.

### Deprecated
- Importing the library as `anura`. The old name still works, resolving to
  the same modules as `pyanura`, but emits a `DeprecationWarning` and will be
  removed in 3.0.

### Removed
- `BleakAVSSClient` and `ProxyAVSSClient`, deprecated since 1.0.0a1. Use
  `BleakAVSSTransport` or `ProxyAVSSTransport` with `AVSSClient` instead.
- The `active` argument of `AVSSClient.report_health`.
- `PROGRAM_STALL_LIMIT`, replaced by `PROGRAM_PROGRESS_TIMEOUT`.
- The CLI `avss write-settings`, replaced by `avss update-settings` and
  `avss reset-settings`.

### Fixed
- `WriteSettingsV2Response.num_unhandled` is optional, as it is on the wire.
  Current node firmware omits it, which made `AVSSClient.write_settings_v2`
  fail.
- `TransceiverClient.dfu_write_image` avoids request sizes that fielded
  transceiver firmware silently drops, which stalled the upgrade.
- `BleakAVSSTransport` no longer matches the response to an abandoned
  control point request to the request that follows it, and handles any
  failure of the control point write, not only `BleakError`.
- `ProxyAVSSTransport.open()` aborts if the transport is closed while it is
  waiting, and no longer leaves its notification subscription and transport
  loop running when it fails.
- A CBOR payload with trailing bytes is rejected instead of being decoded
  from its leading bytes, and `AVSSClient` raises `AVSSProtocolError` for a
  payload it cannot decode instead of letting a `cbor2` exception escape.
- The CLI `avss throughput` and `transceiver avss-throughput` commands
  measure only the report they asked for, not whichever report arrived first.

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

[Unreleased]: https://github.com/ReVibe-Energy/pyanura/compare/v1.1...HEAD
[1.1.0]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0...v1.1
[1.0.0]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a4...v1.0
[1.0.0a4]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a3...v1.0a4
[1.0.0a3]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a2...v1.0a3
[1.0.0a2]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a1...v1.0a2
[1.0.0a1]: https://github.com/ReVibe-Energy/pyanura/compare/v0.14.0...v1.0a1
[0.14.0]: https://github.com/ReVibe-Energy/pyanura/releases/tag/v0.14.0
