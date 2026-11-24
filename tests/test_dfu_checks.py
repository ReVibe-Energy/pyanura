from dataclasses import dataclass

from pyanura.dfu import (
    InstalledImage,
    ManifestV1,
    ManifestV1Image,
    UnmetDependency,
    Version,
    running_images,
    unmet_dependencies,
)


def v(version: str) -> Version:
    return Version.from_string(version)


def image(target_version="2.0", target_build=None, app=None, net=None):
    return ManifestV1Image(
        target_version=target_version,
        path="image.bin",
        target_build=target_build,
        app_version_match=app,
        net_version_match=net,
    )


def running(**versions: str) -> dict[str, InstalledImage]:
    return {name: InstalledImage(v(version)) for name, version in versions.items()}


# ManifestV1Image.matches


def test_matches_compares_builds_when_both_have_one():
    """Dev builds can share a version; the build string tells them apart."""
    built = image("26.5.0.99", target_build="v26.5.0-29-g569757b")

    assert built.matches(InstalledImage(v("26.5.0.99"), "v26.5.0-29-g569757b"))
    assert not built.matches(InstalledImage(v("26.5.0.99"), "v26.5.0-30-gabcdef0"))
    # The build decides even when the versions differ.
    assert built.matches(InstalledImage(v("1.0"), "v26.5.0-29-g569757b"))


def test_matches_compares_versions_without_a_target_build():
    assert image("26.5.0").matches(InstalledImage(v("26.5.0"), "anything"))
    assert not image("26.5.0").matches(InstalledImage(v("26.4.0"), "anything"))


def test_matches_compares_versions_when_the_device_reports_no_build():
    built = image("26.5.0", target_build="v26.5.0")

    for build in (None, ""):
        assert built.matches(InstalledImage(v("26.5.0"), build))
        assert not built.matches(InstalledImage(v("26.4.0"), build))


def test_matches_nothing_when_the_device_reports_no_image():
    assert not image("26.5.0").matches(None)


# running_images


@dataclass
class FirmwareInfo:
    app_version: int = 0
    app_build_version: str = ""
    net_version: int = 0
    net_build_version: str = ""


def test_running_images_reads_both_components():
    info = FirmwareInfo(
        app_version=v("26.5.0").as_numeric(),
        app_build_version="v26.5.0",
        net_version=v("26.4.0.1").as_numeric(),
        net_build_version="v26.4.0-1-gabcdef0",
    )

    assert running_images(info) == {
        "app": InstalledImage(v("26.5.0"), "v26.5.0"),
        "net": InstalledImage(v("26.4.0.1"), "v26.4.0-1-gabcdef0"),
    }


def test_running_images_leaves_out_a_component_with_nothing_to_report():
    """A net core too old to tell the app its version reads as all zero."""
    info = FirmwareInfo(app_version=v("26.5.0").as_numeric(), app_build_version="b")

    assert running_images(info) == {"app": InstalledImage(v("26.5.0"), "b")}


def test_running_images_reads_an_empty_build_as_none():
    info = FirmwareInfo(app_version=v("26.5.0").as_numeric())

    assert running_images(info) == {"app": InstalledImage(v("26.5.0"), None)}


# unmet_dependencies


def test_no_dependencies():
    assert unmet_dependencies([("app", image())], running(app="1.0")) == []
    assert unmet_dependencies([], running()) == []


def test_all_met():
    images = [("app", image(app=">=1.0.0", net=">=3.0.0"))]

    assert unmet_dependencies(images, running(app="1.5.0", net="3.0.0")) == []


def test_every_dependency_is_checked():
    """A met dependency does not vouch for the ones after it."""
    images = [("app", image(app=">=1.0.0", net=">=4.0.0"))]

    unmet = unmet_dependencies(images, running(app="1.5.0", net="3.0.0"))

    assert unmet == [UnmetDependency("app", "net", ">=4.0.0", v("3.0.0"))]
    assert str(unmet[0]) == ("'app' requires 'net' >=4.0.0, running version is 3.0.0.0")


def test_all_unmet_dependencies_are_reported():
    images = [
        ("app", image(app=">=1.8.0")),
        ("net", image(net=">=4.0.0")),
    ]

    unmet = unmet_dependencies(images, running(app="1.5.0", net="3.0.0"))

    assert [(u.image, u.component) for u in unmet] == [("app", "app"), ("net", "net")]


def test_an_unknown_version_is_unmet():
    images = [("app", image(net=">=1.0.0"))]

    unmet = unmet_dependencies(images, running(app="1.0.0"))

    assert unmet == [UnmetDependency("app", "net", ">=1.0.0", None)]
    assert str(unmet[0]) == "'app' requires 'net' >=1.0.0, running version is unknown"


def test_an_empty_specification_is_met_by_an_unknown_version():
    images = [("app", image(net="")), ("net", image(net=None))]

    assert unmet_dependencies(images, running(app="1.0.0")) == []


def test_a_dependency_on_an_earlier_image_is_left_out():
    """The net image needs the new app, which the app image installs first."""
    images = [
        ("app", image("2.0", app="==1.0")),
        ("net", image("2.0", app="==2.0")),
    ]

    assert unmet_dependencies(images, running(app="1.0")) == []


def test_the_rest_of_a_bundle_is_checked_against_what_runs_now():
    """Partway through, the earlier image runs and is no longer listed."""
    manifest = ManifestV1(
        app=image("2.0", app="==1.0"),
        net=image("2.0", app="==2.0"),
    )
    remaining = manifest.images[1:]

    assert unmet_dependencies(remaining, running(app="2.0")) == []
    assert unmet_dependencies(remaining, running(app="1.0")) == [
        UnmetDependency("net", "app", "==2.0", v("1.0"))
    ]


def test_an_image_already_running_is_left_out():
    """It will not be applied, so what it requires does not matter."""
    images = [
        ("app", image("2.0")),
        ("net", image("2.0", net="<2.0")),
    ]

    assert unmet_dependencies(images, running(app="1.0", net="2.0")) == []
    assert unmet_dependencies(images, running(app="1.0", net="1.0")) == []
    assert unmet_dependencies(images, running(app="1.0", net="3.0")) == [
        UnmetDependency("net", "net", "<2.0", v("3.0"))
    ]
