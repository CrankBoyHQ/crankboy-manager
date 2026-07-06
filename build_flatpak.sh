#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

MANIFEST="app.crankboy.crankboy-manager.json"
APP_ID="app.crankboy.crankboy-manager"
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

# The pyserial/certifi/Pillow wheels don't bundle a license file, but
# Flathub requires a license for every module to be installed to
# $FLATPAK_DEST/share/licenses/$FLATPAK_ID. Patch in the upstream license
# text (checked into licenses/) as an extra source per module.
echo ">>> Patching python3-requirements-flatpak.json to install pip package licenses"
python3 - "python3-requirements-flatpak.json" <<'PYEOF'
import json
import sys

path = sys.argv[1]
with open(path) as f:
    manifest = json.load(f)

# module name -> local license file (relative to this manifest's directory)
LICENSES = {
    "python3-pyserial": "licenses/pyserial-LICENSE.txt",
    "python3-certifi": "licenses/certifi-LICENSE.txt",
    "python3-Pillow": "licenses/pillow-LICENSE.txt",
}

for module in manifest["modules"]:
    license_file = LICENSES.get(module["name"])
    if license_file is None:
        continue
    dest_name = f"{module['name']}-LICENSE"
    module["sources"].append({
        "type": "file",
        "path": license_file,
        "dest-filename": dest_name,
    })
    module["build-commands"].append(
        f'install -Dm644 "{dest_name}" '
        f'"${{FLATPAK_DEST}}/share/licenses/${{FLATPAK_ID}}/{module["name"]}/LICENSE"'
    )

with open(path, "w") as f:
    json.dump(manifest, f, indent=4)
    f.write("\n")
PYEOF

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
