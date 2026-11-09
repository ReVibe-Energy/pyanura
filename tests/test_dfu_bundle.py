import io
import json
import zipfile

import pytest

from pyanura.dfu import ManifestV1, ManifestV1Image, read_bundle


def _make_bundle(
    manifest: dict,
    files: dict[str, bytes],
    compression: int = zipfile.ZIP_STORED,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as zf:
        zf.writestr("meta.json", json.dumps(manifest))
        for path, contents in files.items():
            zf.writestr(path, contents)
    return buffer.getvalue()


def test_read_bundle_v1():
    """A manifest as the firmware build currently writes it."""
    bundle = read_bundle(
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
        compatible_device_pns=("VS1-A", "VS1-B"),
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
    assert bundle.images == {"app.bin": b"app firmware", "net.bin": b"net firmware"}


def test_read_bundle_v1_predating_optional_keys():
    """Older v1 bundles lack firmware_pn, compatible_device_pns and target_build."""
    bundle = read_bundle(
        _make_bundle(
            {"version": 1, "app": {"target_version": "24.9.0", "path": "app.bin"}},
            {"app.bin": b"firmware"},
        )
    )

    assert bundle.manifest == ManifestV1(
        app=ManifestV1Image(target_version="24.9.0", path="app.bin")
    )


def test_read_bundle_ignores_unknown_keys():
    bundle = read_bundle(
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


def test_read_bundle_rejects_non_zip():
    with pytest.raises(ValueError, match="not a valid zip archive"):
        read_bundle(b"raw firmware image")


def test_read_bundle_rejects_missing_manifest():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("app.bin", b"firmware")

    with pytest.raises(ValueError, match=r"missing meta\.json"):
        read_bundle(buffer.getvalue())


def test_read_bundle_rejects_invalid_manifest_json():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("meta.json", "{not json")

    with pytest.raises(ValueError, match="not valid JSON"):
        read_bundle(buffer.getvalue())


def test_read_bundle_rejects_non_object_manifest():
    with pytest.raises(ValueError, match="not a JSON object"):
        read_bundle(_make_bundle([1], {}))  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("version", [2, True, 1.0, "1", None])
def test_read_bundle_rejects_unsupported_version(version):
    with pytest.raises(ValueError, match="Unsupported firmware bundle version: "):
        read_bundle(_make_bundle({"version": version}, {}))


def test_read_bundle_rejects_missing_image():
    bundle_bytes = _make_bundle(
        {"version": 1, "app": {"target_version": "26.5.0", "path": "app.bin"}},
        {},
    )

    with pytest.raises(ValueError, match=r"missing the 'app' image: app\.bin"):
        read_bundle(bundle_bytes)


def test_read_bundle_rejects_corrupt_image():
    bundle_bytes = _make_bundle(
        {"version": 1, "app": {"target_version": "26.5.0", "path": "app.bin"}},
        {"app.bin": b"app firmware"},
    )
    # Images are stored uncompressed, so this corrupts the data, not the CRC.
    corrupt = bundle_bytes.replace(b"app firmware", b"app firmwarX")

    with pytest.raises(ValueError, match="'app' image is corrupt"):
        read_bundle(corrupt)


def test_read_bundle_rejects_corrupt_compressed_image():
    image = bytes(range(256)) * 64
    bundle_bytes = _make_bundle(
        {"version": 1, "app": {"target_version": "26.5.0", "path": "app.bin"}},
        {"app.bin": image},
        compression=zipfile.ZIP_DEFLATED,
    )
    # Corrupt the middle of the deflate stream, which follows meta.json's.
    middle = len(bundle_bytes) // 2
    corrupt = bundle_bytes[:middle] + b"\xff" * 16 + bundle_bytes[middle + 16 :]

    with pytest.raises(ValueError, match="'app' image is corrupt"):
        read_bundle(corrupt)


_APP_MANIFEST = {"version": 1, "app": {"target_version": "26.5.0", "path": "app.bin"}}


def test_read_bundle_accepts_image_of_the_maximum_size():
    image = bytes(1024 * 1024)
    bundle = read_bundle(
        _make_bundle(_APP_MANIFEST, {"app.bin": image}, zipfile.ZIP_DEFLATED)
    )

    assert bundle.images["app.bin"] == image


def test_read_bundle_rejects_oversized_image():
    """A zip bomb: compresses to about 1 KiB, decompresses past the bound."""
    bundle_bytes = _make_bundle(
        _APP_MANIFEST, {"app.bin": bytes(1024 * 1024 + 1)}, zipfile.ZIP_DEFLATED
    )

    with pytest.raises(ValueError, match="'app' image is larger than 1048576 bytes"):
        read_bundle(bundle_bytes)


def test_read_bundle_rejects_oversized_manifest():
    manifest = {"version": 1, "note": " " * (32 * 1024)}

    with pytest.raises(ValueError, match=r"meta\.json is larger than 32768 bytes"):
        read_bundle(_make_bundle(manifest, {}, zipfile.ZIP_DEFLATED))


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_read_bundle_rejects_duplicate_entries():
    """Tools disagree on which of two same-named entries to read."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("meta.json", json.dumps(_APP_MANIFEST))
        zf.writestr("app.bin", b"app firmware")
        zf.writestr("app.bin", b"other firmware")

    with pytest.raises(ValueError, match=r"duplicate entries: app\.bin"):
        read_bundle(buffer.getvalue())


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
        (
            {"version": 1, "app": {"target_version": "26.5.0+99", "path": "app.bin"}},
            "'app' 'target_version' is invalid",
        ),
        (
            {
                "version": 1,
                "app": {
                    "target_version": "26.5.0",
                    "path": "app.bin",
                    "net_version_match": "~26.4",
                },
            },
            "'app' 'net_version_match' is invalid",
        ),
    ],
)
def test_read_bundle_rejects_malformed_v1(manifest, message):
    with pytest.raises(ValueError, match=message):
        read_bundle(_make_bundle(manifest, {"app.bin": b"", "net.bin": b""}))
