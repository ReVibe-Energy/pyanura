"""How the CLI interprets a firmware bundle: components and their dependencies.

The library exposes the bundle manifest as written (`pyanura.dfu`); this
module turns it into the components the `upgrade` commands apply.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pyanura.dfu import BundleArchive, ManifestV1, Version, VersionSet, open_bundle


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


def _components_v1(manifest: ManifestV1, bundle: BundleArchive) -> list[Component]:
    components = []

    for name, image in (("app", manifest.app), ("net", manifest.net)):
        if image is None:
            continue

        dependencies = []
        if image.app_version_match is not None:
            dependencies.append(
                Dependency(
                    name="app", version=VersionSet(constraints=image.app_version_match)
                )
            )
        if image.net_version_match is not None:
            dependencies.append(
                Dependency(
                    name="net", version=VersionSet(constraints=image.net_version_match)
                )
            )

        components.append(
            Component(
                name=name,
                version=Version.from_string(image.target_version),
                contents=bundle.read(image.path),
                dependencies=dependencies,
                build=image.target_build,
            )
        )

    return components


def parse_bundle(bundle_bytes: bytes) -> list[Component]:
    """The components of a bundle, in the order they are to be applied.

    Raises:
        ValueError: If the bundle is invalid.
    """
    bundle = open_bundle(bundle_bytes)
    match bundle.manifest:
        case ManifestV1() as manifest:
            return _components_v1(manifest, bundle)


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


@dataclass
class InstalledComponent:
    """The firmware a device reports for one component.

    `version` is None when the device is too old to report a version for the
    component; a dependency on it can then not be verified.
    """

    version: Version | None
    build: str | None


@dataclass(frozen=True)
class UnmetDependency:
    """A dependency the installed firmware does not satisfy."""

    dependency: Dependency
    # The version the device reports, or None if it reports none.
    installed: Version | None

    def __str__(self) -> str:
        if self.installed is None:
            return (
                f"requires '{self.dependency.name}' {self.dependency.version}, "
                f"installed '{self.dependency.name}' version is unknown"
            )
        return (
            f"requires '{self.dependency.name}' {self.dependency.version}, "
            f"device reports {self.installed}"
        )


def unmet_dependencies(
    dependencies: Iterable[Dependency],
    installed: Mapping[str, InstalledComponent],
) -> list[UnmetDependency]:
    """Which of `dependencies` the installed firmware does not satisfy.

    An empty list means every dependency is met. A dependency on a component
    the device reports no version for is unmet: it cannot be verified.

    Callers that want a yes/no answer can use the list's truthiness; callers
    that report a failure can render each entry for a diagnostic.
    """
    unmet = []
    for dep in dependencies:
        current = installed.get(dep.name)
        version = current.version if current is not None else None
        if version is None or version not in dep.version:
            unmet.append(UnmetDependency(dependency=dep, installed=version))
    return unmet
