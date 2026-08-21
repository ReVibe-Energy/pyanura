import io
import json
import zipfile

import pytest

from anura.dfu import (
    Component,
    Dependency,
    Version,
    VersionSet,
    parse_bundle,
    total_dependencies,
)


def _make_dependency(name, constraints):
    return Dependency(name=name, version=VersionSet(constraints=constraints))


def _make_component(name, version, dependencies=None):
    return Component(
        name=name,
        version=Version.from_string(version),
        contents=b"",
        dependencies=dependencies or [],
    )


def _make_bundle(manifest: dict, files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("meta.json", json.dumps(manifest))
        for path, contents in files.items():
            zf.writestr(path, contents)
    return buffer.getvalue()


def test_matches_installed_prefers_build_over_version():
    """When a target build is declared, match on it and ignore the numeric tweak."""
    component = Component(
        name="app",
        version=Version.from_string("26.5.0"),
        contents=b"",
        dependencies=[],
        build="v26.5.0-29-g569757bd7efa",
    )

    # Same build, differing numeric tweak (the dev sentinel) — still a match.
    assert component.matches_installed(
        Version.from_string("26.5.0.99"), "v26.5.0-29-g569757bd7efa"
    )
    # Different build — not the image we flashed.
    assert not component.matches_installed(Version.from_string("26.5.0.99"), "v26.4.0")
    # Device too old to report a build string — fall back to numeric version.
    assert component.matches_installed(Version.from_string("26.5.0"), None)
    assert not component.matches_installed(Version.from_string("26.5.0.99"), None)


def test_matches_installed_falls_back_to_version_without_target_build():
    """Bundles predating target_build match on exact numeric version."""
    component = Component(
        name="app",
        version=Version.from_string("26.5.0"),
        contents=b"",
        dependencies=[],
    )

    assert component.matches_installed(Version.from_string("26.5.0"), None)
    assert not component.matches_installed(Version.from_string("26.5.0.99"), None)
    # The build string is irrelevant when no target build is declared.
    assert component.matches_installed(Version.from_string("26.5.0"), "anything")


def test_parse_bundle_v1():
    bundle_bytes = _make_bundle(
        {
            "version": 1,
            "app": {
                "target_version": "26.5.0",
                "path": "app.bin",
                "net_version_match": ">=26.0.0",
            },
            "net": {"target_version": "26.1.0", "path": "net.bin"},
        },
        {"app.bin": b"app firmware", "net.bin": b"net firmware"},
    )

    bundle = parse_bundle(bundle_bytes)

    # Components come out in app, net order regardless of manifest key order.
    assert [c.name for c in bundle.components] == ["app", "net"]
    app, net = bundle.components
    assert app.version == Version.from_string("26.5.0")
    assert app.contents == b"app firmware"
    assert len(app.dependencies) == 1
    assert app.dependencies[0].name == "net"
    assert Version.from_string("26.0.0") in app.dependencies[0].version
    assert Version.from_string("25.9.9") not in app.dependencies[0].version
    assert net.version == Version.from_string("26.1.0")
    assert net.contents == b"net firmware"
    assert net.dependencies == []


def test_parse_bundle_reads_target_build():
    bundle_bytes = _make_bundle(
        {
            "version": 1,
            "app": {
                "target_version": "26.5.0",
                "target_build": "v26.5.0-29-g569757bd7efa",
                "path": "app.bin",
            },
        },
        {"app.bin": b"firmware"},
    )

    bundle = parse_bundle(bundle_bytes)

    assert len(bundle.components) == 1
    assert bundle.components[0].build == "v26.5.0-29-g569757bd7efa"


def test_parse_bundle_without_target_build_leaves_build_none():
    bundle_bytes = _make_bundle(
        {
            "version": 1,
            "app": {"target_version": "26.5.0", "path": "app.bin"},
        },
        {"app.bin": b"firmware"},
    )

    bundle = parse_bundle(bundle_bytes)

    assert bundle.components[0].build is None


def test_parse_bundle_rejects_non_zip():
    with pytest.raises(ValueError, match="not a valid zip archive"):
        parse_bundle(b"raw firmware image")


def test_parse_bundle_rejects_missing_manifest():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("app.bin", b"firmware")

    with pytest.raises(ValueError, match=r"missing meta\.json"):
        parse_bundle(buffer.getvalue())


def test_parse_bundle_rejects_invalid_manifest_json():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("meta.json", "{not json")

    with pytest.raises(ValueError, match="not valid JSON"):
        parse_bundle(buffer.getvalue())


def test_parse_bundle_rejects_unsupported_version():
    bundle_bytes = _make_bundle({"version": 2}, {})

    with pytest.raises(ValueError, match="Unsupported firmware bundle version"):
        parse_bundle(bundle_bytes)


def test_parse_bundle_rejects_missing_image():
    bundle_bytes = _make_bundle(
        {"version": 1, "app": {"target_version": "26.5.0", "path": "app.bin"}},
        {},
    )

    with pytest.raises(ValueError, match="missing component 'app' image"):
        parse_bundle(bundle_bytes)


def test_total_dependencies_no_components():
    """Empty component list should return empty dependencies."""
    result = total_dependencies([])
    assert result == []


def test_total_dependencies_component_with_no_dependencies():
    result = total_dependencies([_make_component("app", "1.0")])
    assert result == []


def test_total_dependencies_single_dependency():
    result = total_dependencies(
        [
            _make_component(
                "app", "2.0", dependencies=[_make_dependency("app", "==1.0")]
            ),
        ]
    )
    assert len(result) == 1
    assert result[0].name == "app"
    assert result[0].version.constraints == VersionSet(constraints="==1.0").constraints


def test_total_dependencies_multiple_dependencies():
    result = total_dependencies(
        [
            _make_component(
                "app", "2.0", dependencies=[_make_dependency("foo", "==1.0")]
            ),
            _make_component(
                "net", "2.0", dependencies=[_make_dependency("bar", "==2.0")]
            ),
        ]
    )
    assert len(result) == 2
    assert result[0].name == "foo"
    assert result[0].version.constraints == VersionSet(constraints="==1.0").constraints
    assert result[1].name == "bar"
    assert result[1].version.constraints == VersionSet(constraints="==2.0").constraints


def test_total_dependencies_intersection():
    result = total_dependencies(
        [
            _make_component(
                "app", "2.0", dependencies=[_make_dependency("foo", "!=1.0")]
            ),
            _make_component(
                "net", "2.0", dependencies=[_make_dependency("foo", "!=2.0")]
            ),
        ]
    )
    assert len(result) == 1
    assert result[0].name == "foo"
    # The following assertion is awkward and fragile. VersionSet does not
    # support the type of equivalence checking we would prefer to make here.
    assert (
        result[0].version.constraints
        == VersionSet(constraints="!=1.0,!=2.0").constraints
    )


def test_total_dependencies_satisfied_by_preceding_component():
    result = total_dependencies(
        [
            _make_component(
                "app", "2.0", dependencies=[_make_dependency("app", "==1.0")]
            ),
            _make_component(
                "net", "2.0", dependencies=[_make_dependency("app", "==2.0")]
            ),
        ]
    )
    assert len(result) == 1
    assert result[0].name == "app"
    assert result[0].version.constraints == VersionSet(constraints="==1.0").constraints


def test_total_dependencies_all_satisfied_within_bundle():
    result = total_dependencies(
        [
            _make_component("app", "1.0", dependencies=[]),
            _make_component(
                "app", "2.0", dependencies=[_make_dependency("app", "==1.0")]
            ),
            _make_component(
                "net", "2.0", dependencies=[_make_dependency("app", "==2.0")]
            ),
        ]
    )
    assert result == []


def test_total_dependencies_conflicting():
    with pytest.raises(ValueError, match="Conflicting component dependencies"):
        total_dependencies(
            [
                _make_component("app", "1.0", dependencies=[]),
                _make_component(
                    "net", "2.0", dependencies=[_make_dependency("app", "==2.0")]
                ),
            ]
        )
