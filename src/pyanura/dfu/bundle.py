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
from dataclasses import dataclass

__all__ = [
    "BundleArchive",
    "Manifest",
    "ManifestV1",
    "ManifestV1Image",
    "open_bundle",
]


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


class BundleArchive:
    """An opened firmware bundle (provided by open_bundle)."""

    def __init__(self, manifest: Manifest, zip_file: zipfile.ZipFile):
        self.manifest = manifest
        self._zip_file = zip_file

    def read(self, path: str) -> bytes:
        """Read a file from the archive, such as an image named by the manifest.

        Raises:
            KeyError: If the archive has no file at `path`.
        """
        return self._zip_file.read(path)


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


def open_bundle(bundle_bytes: bytes, /) -> BundleArchive:
    """Open a firmware bundle and parse its manifest.

    Keys the manifest version does not define are ignored.

    Raises:
        ValueError: If the bundle is not a zip archive, has no valid manifest
            of a supported version, or lacks an image the manifest names.
    """
    try:
        zip_file = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    except zipfile.BadZipFile as e:
        raise ValueError(f"Bundle is not a valid zip archive: {e}") from e

    try:
        manifest_bytes = zip_file.read("meta.json")
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

    archived = set(zip_file.namelist())
    for name, path in _image_paths(manifest).items():
        if path not in archived:
            raise ValueError(f"Bundle is missing the '{name}' image: {path}")

    return BundleArchive(manifest, zip_file)
