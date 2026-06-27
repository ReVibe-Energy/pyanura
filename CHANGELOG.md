# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

[Unreleased]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a4...HEAD
[1.0.0a4]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a3...v1.0a4
[1.0.0a3]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a2...v1.0a3
[1.0.0a2]: https://github.com/ReVibe-Energy/pyanura/compare/v1.0a1...v1.0a2
[1.0.0a1]: https://github.com/ReVibe-Energy/pyanura/compare/v0.14.0...v1.0a1
[0.14.0]: https://github.com/ReVibe-Energy/pyanura/releases/tag/v0.14.0
