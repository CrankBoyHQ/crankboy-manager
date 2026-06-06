#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

MANIFEST="io.github.crankboyhq.crankboy-manager.json"
APP_ID="io.github.crankboyhq.crankboy-manager"
REQS="requirements-flatpak.txt"

if ! flatpak info org.flatpak.Builder >/dev/null 2>&1; then
    echo "org.flatpak.Builder is not installed." >&2
    echo "Install it with: flatpak install -y flathub org.flatpak.Builder" >&2
    exit 1
fi

# Generate the Flatpak requirements from the single requirements.txt by
# dropping lines tagged NO-FLATPAK (PyQt comes from the Qt runtime/BaseApp).
echo ">>> Generating $REQS from requirements.txt"
grep -v 'NO-FLATPAK' requirements.txt > "$REQS"

echo ">>> Generating python3-requirements-flatpak.json"
flatpak run --command=flatpak-pip-generator org.flatpak.Builder --requirements-file="$REQS"

echo ">>> Building flatpak"
flatpak run --command=flathub-build org.flatpak.Builder "$MANIFEST"

echo ">>> Linting manifest"
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest "$MANIFEST" || true

echo ">>> Linting repo"
flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo || true

REPO_DIR="$(pwd)/repo"

cat <<EOF

Build complete.

To install:
    flatpak install --user --reinstall -y "$REPO_DIR" $APP_ID

To launch:
    flatpak run $APP_ID
EOF
