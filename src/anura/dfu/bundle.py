"""
DFU (Device Firmware Update) bundle data model for Anura devices.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections import defaultdict
from dataclasses import dataclass

from .version import Version, VersionSet

__all__ = [
    "Bundle",
    "Component",
    "Dependency",
    "parse_bundle",
    "total_dependencies",
]


@dataclass
class Dependency:
    name: str
    version: VersionSet


@dataclass
class Component:
    """A single flashable image inside a bundle"""

    name: str
    version: Version
    contents: bytes
    dependencies: list[Dependency]
    # Unique build identifier this image was produced from. Bundles that
    # predate the `target_build` manifest key leave this None and fall back
    # to numeric version matching.
    build: str | None = None

    def matches_installed(self, version: Version | None, build: str | None) -> bool:
        """Whether a device reporting (version, build) is running this component.

        Prefer the build string when both the bundle declares a target build
        and the device reports one: it is unique even for dev-builds, unlike
        the numeric version
        """
        if self.build is not None and build is not None:
            return build == self.build
        return version == self.version


@dataclass
class Bundle:
    """Bundle: a parsed firmware bundle (provided by parse_bundle)"""

    components: list[Component]


def total_dependencies(components: list[Component]) -> list[Dependency]:
    """Calculate total dependencies of a list of components.

    This routine takes into account that a component may have its dependencies
    satisfied by or falsified by a preceding component in the list.

    This does not detect if the bundle dependencies as a whole are
    unsatisfiable. It is up to the DFU routine to check if the device
    meets the sum total dependencies of the bundle before initiating
    the DFU operation.

    Raises:
        ValueError if bundle contains conflicting dependencies
    """
    dependencies = defaultdict(lambda: VersionSet(constraints=None))
    installed_components = {}

    for component in components:
        for dep in component.dependencies:
            if dep.name in installed_components:
                if installed_components[dep.name].version not in dep.version:
                    raise ValueError("Conflicting component dependencies")
            else:
                dependencies[dep.name] = dependencies[dep.name].intersection(
                    dep.version
                )
        installed_components[component.name] = component

    return [Dependency(name, version) for name, version in dependencies.items()]


def _parse_bundle_v1(manifest: dict, bundle: zipfile.ZipFile) -> Bundle:
    components = []

    for name in ["app", "net"]:
        if name not in manifest:
            continue

        data = manifest[name]

        version = Version.from_string(data["target_version"])
        build = data.get("target_build")
        try:
            contents = bundle.read(data["path"])
        except KeyError as e:
            raise ValueError(
                f"Bundle is missing component '{name}' image: {data['path']}"
            ) from e
        dependencies = []

        if (app_version := data.get("app_version_match")) is not None:
            dependencies.append(
                Dependency(name="app", version=VersionSet(constraints=app_version))
            )

        if (net_version := data.get("net_version_match")) is not None:
            dependencies.append(
                Dependency(name="net", version=VersionSet(constraints=net_version))
            )

        components.append(
            Component(
                name=name,
                version=version,
                contents=contents,
                dependencies=dependencies,
                build=build,
            )
        )

    return Bundle(components=components)


def parse_bundle(bundle_bytes: bytes, /) -> Bundle:
    """Parse a firmware bundle."""
    try:
        bundle_zip = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    except zipfile.BadZipFile as e:
        raise ValueError(f"Bundle is not a valid zip archive: {e}") from e

    with bundle_zip as bundle:
        try:
            manifest_bytes = bundle.read("meta.json")
        except KeyError as e:
            raise ValueError("Bundle is missing meta.json at the archive root") from e
        try:
            manifest = json.loads(manifest_bytes)
        except json.JSONDecodeError as e:
            raise ValueError(f"Bundle meta.json is not valid JSON: {e}") from e

        version = manifest.get("version")
        if version == 1:
            return _parse_bundle_v1(manifest, bundle)
        raise ValueError(f"Unsupported firmware bundle version: {version!r}")
