import io
import json
import zipfile

import pytest

from pyanura.dfu import ManifestV1, ManifestV1Image, open_bundle


def _make_bundle(manifest: dict, files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("meta.json", json.dumps(manifest))
        for path, contents in files.items():
            zf.writestr(path, contents)
    return buffer.getvalue()


def test_open_bundle_v1():
    """A manifest as the firmware build currently writes it."""
    bundle = open_bundle(
        _make_bundle(
            {
                "note": "This file is used for firmware update bundles.",
                "version": 1,
                "firmware_pn": "FW-1001",
                "compatible_device_pns": ["VS1-A", "VS1-B"],
                "app": {
                    "target_version": "26.5.0",
                    "target_build": "v26.5.0-29-g569757bd7efa",
                    "path": "app.bin",
                    "app_version_match": ">=24.9.0",
                    "net_version_match": ">=24.9.0",
                },
                "net": {
                    "target_version": "26.5.0",
                    "target_build": "v26.5.0-29-g569757bd7efa",
                    "path": "net.bin",
                    "app_version_match": "==26.5.0",
                    "net_version_match": None,
                },
            },
            {"app.bin": b"app firmware", "net.bin": b"net firmware"},
        )
    )

    assert bundle.manifest == ManifestV1(
        firmware_pn="FW-1001",
        compatible_device_pns=["VS1-A", "VS1-B"],
        app=ManifestV1Image(
            target_version="26.5.0",
            path="app.bin",
            target_build="v26.5.0-29-g569757bd7efa",
            app_version_match=">=24.9.0",
            net_version_match=">=24.9.0",
        ),
        net=ManifestV1Image(
            target_version="26.5.0",
            path="net.bin",
            target_build="v26.5.0-29-g569757bd7efa",
            app_version_match="==26.5.0",
            net_version_match=None,
        ),
    )
    assert bundle.read("app.bin") == b"app firmware"
    assert bundle.read("net.bin") == b"net firmware"


def test_open_bundle_v1_predating_optional_keys():
    """Older v1 bundles lack firmware_pn, compatible_device_pns and target_build."""
    bundle = open_bundle(
        _make_bundle(
            {"version": 1, "app": {"target_version": "24.9.0", "path": "app.bin"}},
            {"app.bin": b"firmware"},
        )
    )

    assert bundle.manifest == ManifestV1(
        app=ManifestV1Image(target_version="24.9.0", path="app.bin")
    )


def test_open_bundle_ignores_unknown_keys():
    bundle = open_bundle(
        _make_bundle(
            {
                "version": 1,
                "future_key": {"anything": True},
                "app": {
                    "target_version": "26.5.0",
                    "path": "app.bin",
                    "future_image_key": 1,
                },
            },
            {"app.bin": b"firmware"},
        )
    )

    assert bundle.manifest.app == ManifestV1Image(
        target_version="26.5.0", path="app.bin"
    )


def test_open_bundle_rejects_non_zip():
    with pytest.raises(ValueError, match="not a valid zip archive"):
        open_bundle(b"raw firmware image")


def test_open_bundle_rejects_missing_manifest():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("app.bin", b"firmware")

    with pytest.raises(ValueError, match=r"missing meta\.json"):
        open_bundle(buffer.getvalue())


def test_open_bundle_rejects_invalid_manifest_json():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("meta.json", "{not json")

    with pytest.raises(ValueError, match="not valid JSON"):
        open_bundle(buffer.getvalue())


def test_open_bundle_rejects_non_object_manifest():
    with pytest.raises(ValueError, match="not a JSON object"):
        open_bundle(_make_bundle([1], {}))  # pyright: ignore[reportArgumentType]


def test_open_bundle_rejects_unsupported_version():
    with pytest.raises(ValueError, match="Unsupported firmware bundle version: 2"):
        open_bundle(_make_bundle({"version": 2}, {}))


def test_open_bundle_rejects_missing_image():
    bundle_bytes = _make_bundle(
        {"version": 1, "app": {"target_version": "26.5.0", "path": "app.bin"}},
        {},
    )

    with pytest.raises(ValueError, match=r"missing the 'app' image: app\.bin"):
        open_bundle(bundle_bytes)


@pytest.mark.parametrize(
    "manifest, message",
    [
        (
            {"version": 1, "app": {"path": "app.bin"}},
            "'app' is missing 'target_version'",
        ),
        ({"version": 1, "app": {"target_version": "1.0"}}, "'app' is missing 'path'"),
        ({"version": 1, "app": "app.bin"}, "'app' is not an object"),
        (
            {"version": 1, "net": {"target_version": 1, "path": "net.bin"}},
            "'net' 'target_version' is not a string",
        ),
        ({"version": 1, "firmware_pn": 1001}, "'firmware_pn' is not a string"),
        (
            {"version": 1, "compatible_device_pns": "VS1-A"},
            "'compatible_device_pns' is not a list of strings",
        ),
    ],
)
def test_open_bundle_rejects_malformed_v1(manifest, message):
    with pytest.raises(ValueError, match=message):
        open_bundle(_make_bundle(manifest, {"app.bin": b"", "net.bin": b""}))
