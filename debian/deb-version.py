#!/usr/bin/env python3
"""Map a pyanura PEP 440 version to a Debian package version.

The upstream version is supplied on the command line.

    1.0a4                    ->  1.0~a4-1
    1.0                      ->  1.0-1
    1.2.dev8+20260716.gdeadb ->  1.2~~dev8+20260716.gdeadb-1

Pre-releases become ``~aN`` (they sort before the final release); dev/snapshot
releases become ``~~devN`` (an empty pre-release segment sorts before any real
one, so snapshots sort below every pre-release and the final release). The
Debian revision is appended as ``-N``.
"""

import argparse

from packaging.version import InvalidVersion, Version


def debian_version(ver: Version, revision: str = "1") -> str:
    assert ver.epoch == 0, "epochs are not supported"
    assert not ver.is_postrelease, "post-releases are not supported"

    version = ".".join(map(str, ver.release))

    pre = "" if ver.pre is None else f"{ver.pre[0]}{ver.pre[1]}"
    if ver.dev is not None:
        version += f"~{pre}~dev{ver.dev}"
    elif pre:
        version += f"~{pre}"

    if ver.local:
        version += f"+{ver.local}"

    return f"{version}-{revision}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "version", help="upstream PEP 440 version, e.g. 1.2 or 1.2.dev8+..."
    )
    ap.add_argument("--revision", default="1", help="Debian revision (default: 1)")
    args = ap.parse_args()

    try:
        ver = Version(args.version)
    except InvalidVersion as e:
        ap.error(f"invalid PEP 440 version {args.version!r}: {e}")

    print(debian_version(ver, args.revision))
