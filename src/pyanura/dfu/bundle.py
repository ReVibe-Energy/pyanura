"""
DFU (Device Firmware Update) bundles for Anura devices.

A bundle is a zip archive with a ``meta.json`` manifest at its root. The
manifest is versioned; each version is represented by its own type mirroring
the JSON as written, and interpreting it is left to the caller.
"""

from __future__ import annotations

import io
import json
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass

__all__ = [
    "Bundle",
    "Manifest",
    "ManifestV1",
    "ManifestV1Image",
    "read_bundle",
]

# Bounds on the decompressed size of what read_bundle reads, so that a small
# but highly compressed archive can't exhaust memory.
_MAX_MANIFEST_SIZE = 32 * 1024
_MAX_IMAGE_SIZE = 1024 * 1024


@dataclass(frozen=True)
class ManifestV1Image:
    """One image ("app" or "net") in a version 1 manifest."""

    target_version: str
    path: str
    # Optional keys: bundles predating `target_build` leave it out, and a
    # `*_version_match` that is absent or null means no constraint.
    target_build: str | None = None
    app_version_match: str | None = None
    net_version_match: str | None = None


@dataclass(frozen=True)
class ManifestV1:
    """A version 1 manifest.

    `firmware_pn` and `compatible_device_pns` were added to version 1 later
    and are None for bundles that predate them.
    """

    firmware_pn: str | None = None
    compatible_device_pns: list[str] | None = None
    app: ManifestV1Image | None = None
    net: ManifestV1Image | None = None


# Union of the supported manifest versions.
Manifest = ManifestV1


@dataclass(frozen=True)
class Bundle:
    """A firmware bundle (provided by read_bundle)."""

    manifest: Manifest
    # The images the manifest names, keyed by their path in the archive.
    images: dict[str, bytes]


def _optional_str(obj: dict, key: str, where: str) -> str | None:
    value = obj.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Bundle manifest {where}'{key}' is not a string")
    return value


def _str(obj: dict, key: str, where: str) -> str:
    value = _optional_str(obj, key, where)
    if value is None:
        raise ValueError(f"Bundle manifest {where}is missing '{key}'")
    return value


def _parse_image_v1(name: str, data: object) -> ManifestV1Image:
    where = f"'{name}' "
    if not isinstance(data, dict):
        raise ValueError(f"Bundle manifest {where}is not an object")
    return ManifestV1Image(
        target_version=_str(data, "target_version", where),
        path=_str(data, "path", where),
        target_build=_optional_str(data, "target_build", where),
        app_version_match=_optional_str(data, "app_version_match", where),
        net_version_match=_optional_str(data, "net_version_match", where),
    )


def _parse_manifest_v1(manifest: dict) -> ManifestV1:
    pns = manifest.get("compatible_device_pns")
    if pns is not None and not (
        isinstance(pns, list) and all(isinstance(pn, str) for pn in pns)
    ):
        raise ValueError(
            "Bundle manifest 'compatible_device_pns' is not a list of strings"
        )

    images = {
        name: _parse_image_v1(name, manifest[name])
        for name in ("app", "net")
        if manifest.get(name) is not None
    }

    return ManifestV1(
        firmware_pn=_optional_str(manifest, "firmware_pn", ""),
        compatible_device_pns=pns,
        app=images.get("app"),
        net=images.get("net"),
    )


def _image_paths(manifest: Manifest) -> dict[str, str]:
    return {
        name: image.path
        for name, image in (("app", manifest.app), ("net", manifest.net))
        if image is not None
    }


def _read_bounded(
    zip_file: zipfile.ZipFile, path: str, limit: int, label: str
) -> bytes:
    """Read a file from the archive, decompressing no more than `limit` bytes.

    Raises:
        KeyError: If the archive has no file at `path`.
        ValueError: If the file is corrupt or larger than `limit`.
    """
    try:
        with zip_file.open(path) as f:
            # Bounded by what is actually decompressed, not by the size the
            # archive declares for the file.
            data = f.read(limit + 1)
    except (zipfile.BadZipFile, zlib.error, EOFError) as e:
        raise ValueError(f"Bundle {label} is corrupt: {e}") from e
    except (NotImplementedError, RuntimeError) as e:
        # Unsupported compression method, or encryption.
        raise ValueError(f"Bundle {label} cannot be read: {e}") from e
    if len(data) > limit:
        raise ValueError(f"Bundle {label} is larger than {limit} bytes")
    return data


def read_bundle(bundle_bytes: bytes, /) -> Bundle:
    """Read a firmware bundle: its manifest and the images it names.

    Keys the manifest version does not define are ignored, as are files in
    the archive the manifest does not name.

    Raises:
        ValueError: If the bundle is not a zip archive, has duplicate entry
            names, has no valid manifest of a supported version, or lacks an
            image the manifest names or has one that is corrupt or too large
            (32 KiB for the manifest, 1 MiB for each image).
    """
    try:
        zip_file = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    except zipfile.BadZipFile as e:
        raise ValueError(f"Bundle is not a valid zip archive: {e}") from e

    with zip_file:
        # A name that appears twice could be read as either entry, depending
        # on the tool reading the archive.
        counts = Counter(zip_file.namelist())
        duplicates = sorted(name for name, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"Bundle has duplicate entries: {', '.join(duplicates)}")

        try:
            manifest_bytes = _read_bounded(
                zip_file, "meta.json", _MAX_MANIFEST_SIZE, "meta.json"
            )
        except KeyError as e:
            raise ValueError("Bundle is missing meta.json at the archive root") from e
        try:
            manifest_json = json.loads(manifest_bytes)
        except json.JSONDecodeError as e:
            raise ValueError(f"Bundle meta.json is not valid JSON: {e}") from e
        if not isinstance(manifest_json, dict):
            raise ValueError("Bundle meta.json is not a JSON object")

        version = manifest_json.get("version")
        if version == 1:
            manifest = _parse_manifest_v1(manifest_json)
        else:
            raise ValueError(f"Unsupported firmware bundle version: {version!r}")

        images = {}
        for name, path in _image_paths(manifest).items():
            try:
                images[path] = _read_bounded(
                    zip_file, path, _MAX_IMAGE_SIZE, f"'{name}' image"
                )
            except KeyError as e:
                raise ValueError(f"Bundle is missing the '{name}' image: {path}") from e

    return Bundle(manifest, images)
