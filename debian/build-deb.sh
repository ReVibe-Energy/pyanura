#!/usr/bin/env bash
#
# Build the pyanura Debian package from a pyanura sdist.
#
# Usage:
#   debian/build-deb.sh [SDIST] [DIST]
#
#   SDIST  path to the pyanura sdist (pyanura-<version>.tar.gz). Defaults to the
#          newest dist/pyanura-*.tar.gz, i.e. the output of `uv build`.
#   DIST   Debian suite to build for (default: trixie).
#
# Environment:
#   DEB_REVISION          Debian revision      (default: 1)
#   DEBEMAIL/DEBFULLNAME  changelog identity    (defaults provided)
#
# Requires an sbuild chroot tarball at ~/.cache/sbuild/<DIST>-<arch>.tar.gz:
#   mmdebstrap --variant=buildd trixie \
#       ~/.cache/sbuild/trixie-$(dpkg --print-architecture).tar.gz
set -euo pipefail

PKG="pyanura"
DIST="${2:-trixie}"
ARCH="$(dpkg --print-architecture)"
DEB_REVISION="${DEB_REVISION:-1}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO_ROOT/build"
DIST_DIR="$REPO_ROOT/dist"
CHROOT_TARBALL="$HOME/.cache/sbuild/${DIST}-${ARCH}.tar.gz"

export DEBEMAIL="${DEBEMAIL:-info@revibeenergy.com}"
export DEBFULLNAME="${DEBFULLNAME:-ReVibe Energy}"

# 0. Resolve the sdist (explicit arg, or newest in dist/).
SDIST="${1:-}"
if [ -z "$SDIST" ]; then
    SDIST="$(ls -1t "$DIST_DIR/$PKG"-*.tar.gz 2>/dev/null | head -n1 || true)"
    [ -n "$SDIST" ] || { echo "Error: no sdist given and none found in $DIST_DIR (run 'uv build')"; exit 1; }
fi
[ -f "$SDIST" ] || { echo "Error: sdist not found: $SDIST"; exit 1; }
SDIST="$(cd "$(dirname "$SDIST")" && pwd)/$(basename "$SDIST")"

if [ ! -f "$CHROOT_TARBALL" ]; then
    echo "Error: sbuild chroot not found at $CHROOT_TARBALL"
    exit 1
fi

mkdir -p "$BUILD_DIR" "$DIST_DIR"

# 1. Derive versions. The sdist filename carries the upstream PEP 440 version.
base="$(basename "$SDIST")"
PEP_VERSION="${base#"$PKG"-}"
PEP_VERSION="${PEP_VERSION%.tar.gz}"
VER_FULL="$(python3 "$REPO_ROOT/debian/deb-version.py" "$PEP_VERSION" --revision "$DEB_REVISION")"
VER_UPSTREAM="${VER_FULL%-*}"   # strip the Debian revision
echo "--- Upstream: $PEP_VERSION -> Debian: $VER_FULL ---"

# 2. The sdist becomes the orig tarball (renamed to the Debian upstream version).
UPSTREAM_TARBALL="$BUILD_DIR/${PKG}_${VER_UPSTREAM}.orig.tar.gz"
cp -f "$SDIST" "$UPSTREAM_TARBALL"

# 3. Unpack the orig and overlay debian/ (dpkg-source needs a tree holding the
#    orig contents plus debian/). Building in a temp tree keeps the diff empty.
echo "--- Preparing source tree ---"
SOURCE_TMP="$BUILD_DIR/source-temp"
rm -rf "$SOURCE_TMP"
mkdir -p "$SOURCE_TMP"
tar -xzf "$UPSTREAM_TARBALL" -C "$SOURCE_TMP" --strip-components=1
cp -a "$REPO_ROOT/debian" "$SOURCE_TMP/"

# 4. Stamp a throwaway changelog entry.
echo "--- Writing changelog entry $VER_FULL ---"
( cd "$SOURCE_TMP" && dch \
    --newversion "$VER_FULL" \
    --distribution "$DIST" --force-distribution \
    "Automated build of pyanura ${PEP_VERSION}" )

# 5. Generate the source package (.dsc) into build/.
echo "--- Generating source package (.dsc) ---"
( cd "$SOURCE_TMP" && dpkg-source -b . )

# 6. Build in a clean chroot with sbuild
echo "--- Starting sbuild ($DIST) ---"
sbuild --chroot-mode=unshare \
    --chroot "$CHROOT_TARBALL" \
    --build-dir="$BUILD_DIR" \
    --dist="$DIST" \
    --debbuildopts="-us -uc" \
    "$BUILD_DIR/${PKG}_${VER_FULL}.dsc"

# 7. Collect the final package.
echo "--- Collecting packages to $DIST_DIR ---"
cp "$BUILD_DIR"/*.deb "$DIST_DIR/"
echo "--- Done: ---"
ls -1 "$DIST_DIR"/*.deb
