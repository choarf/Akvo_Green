#!/usr/bin/env bash
# Builds the AKVO Modbus Tool (desktop Modbus RTU scanner/reader/writer, a
# separate repo) into a .deb from its own packaging/debian skeleton, so
# push_site.sh can install it on a Pi next to the gateway.
#
#   tools/build_modbus_tool.sh <out-dir>
#
# Prints the .deb path, then a hash of the tool's source on the second line
# (push_site.sh compares it with the one installed on the Pi and skips the
# install when nothing changed). The tool is looked up at $AKVO_MODBUS_DIR,
# default ../Tools/AkvoModbus next to this repo. Exits 3 if it isn't there.
set -euo pipefail

OUT="${1:?usage: tools/build_modbus_tool.sh <out-dir>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOL="${AKVO_MODBUS_DIR:-$ROOT/../Tools/AkvoModbus}"
[ -f "$TOOL/src/main.py" ] && [ -f "$TOOL/packaging/debian/DEBIAN/control" ] || exit 3
TOOL="$(cd "$TOOL" && pwd)"
command -v dpkg-deb >/dev/null || { echo "error: dpkg-deb not found (sudo apt install dpkg)" >&2; exit 1; }

# Only the app's own sources: no PyInstaller output, release zips, caches or logs.
FILES="$(cd "$TOOL/src" && find . -type f \( -name '*.py' -o -name 'requirements.txt' \) \
  -not -path './build*' -not -path './dist/*' -not -path '*/__pycache__/*' | LC_ALL=C sort)"
HASH="$( (cd "$TOOL/src" && echo "$FILES" | xargs sha256sum; cd "$TOOL/packaging/debian" && find . -type f | LC_ALL=C sort | xargs sha256sum) \
  | sha256sum | cut -c1-16)"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -a "$TOOL/packaging/debian/." "$STAGE/"
mkdir -p "$STAGE/opt/akvo-modbus"
(cd "$TOOL/src" && echo "$FILES" | xargs cp --parents -t "$STAGE/opt/akvo-modbus")
echo "$HASH" > "$STAGE/opt/akvo-modbus/.source-hash"
chmod -R u=rwX,go=rX "$STAGE"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm" "$STAGE/usr/bin/"*

VERSION="$(sed -n 's/^Version: *//p' "$STAGE/DEBIAN/control")"
mkdir -p "$OUT"
DEB="$(cd "$OUT" && pwd)/akvo-modbus_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$DEB" >/dev/null
echo "$DEB"
echo "$HASH"
