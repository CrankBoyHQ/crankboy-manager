#!/usr/bin/env python3
"""
Build script for CrankBoy Manager standalone executables.

Uses PyInstaller to create standalone executables for Windows, macOS, and Linux.
"""

import os
import sys
import shutil
import subprocess
import platform
from pathlib import Path


# Build configuration
APP_NAME = "CrankBoyManager"
APP_DISPLAY_NAME = "CrankBoy Manager"
MAIN_SCRIPT = "main.py"

# Import version from centralized module
import sys
sys.path.insert(0, str(Path(__file__).parent))
from src.version import VERSION

# Platform-specific icon files
ICON_FILE_WINDOWS = "src/assets/AppIcon.ico"
ICON_FILE_MACOS = "src/assets/AppIcon.icns"
ICON_FILE_LINUX = "src/assets/AppIcon.png"


def generate_version_module():
    """Generate _version_built.py with hardcoded version for PyInstaller builds.

    This allows the built executable to have the version hardcoded at build time,
    avoiding file I/O operations in production.
    """
    version_module_path = Path(__file__).parent / "src" / "_version_built.py"
    with open(version_module_path, 'w') as f:
        f.write(f'"""Auto-generated version module. Do not edit."""\n')
        f.write(f'VERSION = "{VERSION}"\n')
    print(f"Generated {version_module_path} with version {VERSION}")


def get_platform():
    """Get the current platform."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    return system


def clean_build():
    """Clean previous build artifacts."""
    print("Cleaning previous builds...")
    dirs_to_remove = ["build", "dist", "__pycache__"]
    for dir_name in dirs_to_remove:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)
            print(f"  Removed {dir_name}/")

    # Clean generated version module
    version_built = Path(__file__).parent / "src" / "_version_built.py"
    if version_built.exists():
        version_built.unlink()
        print(f"  Removed {version_built.name}")

    # Clean .pyc files
    for pyc_file in Path(".").rglob("*.pyc"):
        pyc_file.unlink()
    for pycache in Path(".").rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache)


def build_windows():
    """Build Windows executable."""
    print("\n=== Building Windows executable ===")

    # Generate version module with hardcoded version
    generate_version_module()

    cmd = [
        "pyinstaller",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--add-data", "src;src",
        "--add-data", "db;db",
        "--hidden-import", "serial",
        "--hidden-import", "serial.tools.list_ports",
        "--hidden-import", "certifi",
        "--hidden-import", "PIL",
        "--clean",
    ]

    if ICON_FILE_WINDOWS and os.path.exists(ICON_FILE_WINDOWS):
        cmd.extend(["--icon", ICON_FILE_WINDOWS])

    cmd.append(MAIN_SCRIPT)

    subprocess.run(cmd, check=True)

    # Create ZIP archive
    zip_name = f"{APP_NAME}-{VERSION}-windows.zip"
    print(f"\nCreating {zip_name}...")
    shutil.make_archive(
        f"dist/{APP_NAME}-{VERSION}-windows",
        'zip',
        'dist',
        APP_NAME + '.exe'
    )

    print(f"[OK] Build complete: dist/{APP_NAME}.exe")
    print(f"[OK] Archive created: dist/{zip_name}")


# PyInstaller spec template for macOS bundles. Driving the build through a
# spec (rather than --windowed/--icon CLI flags) lets us set `version` on the
# EXE (drives CFBundleShortVersionString via BUNDLE) and inject CFBundleVersion
# through BUNDLE(info_plist={...}). PyInstaller merges info_plist as a dict
# (update()), and CFBundleVersion isn't in its base dict, so this survives.
# Fields:
#   name              - app/bundle name (a string literal)
#   version           - version string literal (e.g. '1.1.0')
#   bundle_identifier - CFBundleIdentifier value
#   icon              - path to the .icns (string literal)
_MACOS_SPEC_TEMPLATE = """# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['{main_script}'],
    pathex=[],
    binaries=[],
    datas=[('src', 'src'), ('db', 'db')],
    hiddenimports=['serial', 'serial.tools.list_ports', 'certifi', 'PIL'],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name={name},
    debug=False,
    bootloader_ignore_signals=False,
    strip={strip},
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version={version},
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name={name},
)
app = BUNDLE(
    coll,
    name={name} + '.app',
    icon={icon},
    bundle_identifier={bundle_identifier},
    version={version},
    info_plist={{'CFBundleVersion': {version}}},
)
"""


def build_macos():
    """Build macOS app bundle."""
    print("\n=== Building macOS app bundle ===")

    # Generate version module with hardcoded version
    generate_version_module()

    # Inject CFBundleShortVersionString/CFBundleVersion so Get Info is
    # correct. PyInstaller only honours these from the EXE/BUNDLE `version`
    # args, not info_plist, so we drive them via a generated spec.
    spec = Path(__file__).parent / ("macos-" + APP_DISPLAY_NAME.replace(" ", "_") + ".spec")
    spec.write_text(_MACOS_SPEC_TEMPLATE.format(
        main_script=MAIN_SCRIPT,
        name=repr(APP_DISPLAY_NAME),
        version=repr(VERSION),
        bundle_identifier=repr("com.crankboy.crankboy-manager"),
        icon=repr("src/assets/AppIcon.icns"),
        strip=False,
    ))
    print(f"Generated {spec.name}")

    subprocess.run(["pyinstaller", "--clean", str(spec)], check=True)
    spec.unlink()

    # Create ZIP archive
    zip_name = f"{APP_NAME}-{VERSION}-macos.zip"
    print(f"\nCreating {zip_name}...")
    shutil.make_archive(
        f"dist/{APP_NAME}-{VERSION}-macos",
        'zip',
        'dist',
        APP_DISPLAY_NAME + '.app'
    )

    print(f"[OK] Build complete: dist/{APP_DISPLAY_NAME}.app")
    print(f"[OK] Archive created: dist/{zip_name}")

    # Create ZIP archive
    zip_name = f"{APP_NAME}-{VERSION}-macos.zip"
    print(f"\nCreating {zip_name}...")
    shutil.make_archive(
        f"dist/{APP_NAME}-{VERSION}-macos",
        'zip',
        'dist',
        APP_DISPLAY_NAME + '.app'
    )

    print(f"[OK] Build complete: dist/{APP_DISPLAY_NAME}.app")
    print(f"[OK] Archive created: dist/{zip_name}")


def build_linux(flatpak=False):
    """Build Linux AppImage or for Flatpak"""
    if flatpak:
        print("\n=== Building for Flatpak ===")
        app_id = os.environ.get("FLATPAK_ID")
        binary_name = "crankboy-manager"
        appdir = os.environ.get("FLATPAK_DEST")
    else:
        print("\n=== Building Linux AppImage ===")
        app_id = APP_NAME.lower()
        binary_name = APP_NAME.lower()
        appdir = f"dist/{APP_NAME}.AppDir"

    # Bake VERSION (read from .version) into src/_version_built.py so the
    # runtime import in src/version.py finds it. Both build paths use
    # this -- PyInstaller picks it up via --add-data src:src on the
    # AppImage path, and the Flatpak path copies the whole src/ tree
    # to /app/share/.../src/ via shutil.copytree.
    generate_version_module()

    if not flatpak:
        cmd = [
            "pyinstaller",
            "--onefile",
            "--windowed",
            "--name", APP_NAME.lower(),
            "--add-data", "src:src",
            "--add-data", "db:db",
            "--hidden-import", "serial",
            "--hidden-import", "serial.tools.list_ports",
            "--hidden-import", "certifi",
            "--hidden-import", "PIL",
            "--clean",
        ]

        if ICON_FILE_LINUX and os.path.exists(ICON_FILE_LINUX):
            cmd.extend(["--icon", ICON_FILE_LINUX])

        cmd.append(MAIN_SCRIPT)

        subprocess.run(cmd, check=True)

    if flatpak:
        app_share = f"{appdir}/share/{app_id}"
        os.makedirs(app_share, exist_ok=True)
        shutil.copytree("src", f"{app_share}/src")
        shutil.copy(MAIN_SCRIPT, f"{app_share}/{MAIN_SCRIPT}")
        shutil.copytree("db", f"{app_share}/db")
        exec_target = f"python3 {app_share}/{MAIN_SCRIPT}"
        launcher_path = f"{appdir}/bin/{binary_name}"

        # Flathub requires the license of every module to be installed to
        # $FLATPAK_DEST/share/licenses/$FLATPAK_ID.
        license_dir = f"{appdir}/share/licenses/{app_id}"
        os.makedirs(license_dir, exist_ok=True)
        shutil.copy("LICENSE", f"{license_dir}/LICENSE")
    else:
        # Create AppDir structure for AppImage
        os.makedirs(appdir, exist_ok=True)
        os.makedirs(f"{appdir}/usr/bin", exist_ok=True)

        # Copy executable
        shutil.copy(f"dist/{APP_NAME.lower()}", f"{appdir}/usr/bin/")
        exec_target = f'"${{APPDIR}}/usr/bin/{binary_name}"' # APPDIR is provided by AppImage runtime
        launcher_path = f"{appdir}/AppRun"

    # Create AppRun script
    with open(launcher_path, "w") as f:
        f.write(f"""#!/bin/bash
exec {exec_target} "$@"
""")
    os.chmod(launcher_path, 0o755)

    # Create desktop entry
    if flatpak:
        desktop_dir = f"{appdir}/share/applications"
        os.makedirs(desktop_dir, exist_ok=True)
        desktop_path = f"{desktop_dir}/{app_id}.desktop"
    else:
        desktop_path = f"{appdir}/{app_id}.desktop"

    desktop = f"""[Desktop Entry]
Name={APP_DISPLAY_NAME}
Exec={binary_name}
Icon={app_id}
Type=Application
Categories=Utility;
Comment=Transfer Game Boy ROMs to CrankBoy
"""
    with open(desktop_path, "w") as f:
        f.write(desktop)

    # Create AppStream metadata from the committed metainfo file (single
    # source of truth). Flathub reads the changelog from the <releases>
    # block here; AppImage also ships this file for consistency.
    metainfo_dir = f"{appdir}/share/metainfo" if flatpak else f"{appdir}/usr/share/metainfo"
    os.makedirs(metainfo_dir, exist_ok=True)
    shutil.copy(
        "app.crankboy.crankboy-manager.metainfo.xml",
        f"{metainfo_dir}/{app_id}.metainfo.xml",
    )

    # Copy the icon file
    if ICON_FILE_LINUX and os.path.exists(ICON_FILE_LINUX):
        icon_dir = f"{appdir}/share/icons/hicolor/256x256/apps" if flatpak \
            else f"{appdir}/usr/share/icons/hicolor/256x256/apps"
        os.makedirs(icon_dir, exist_ok=True)
        shutil.copy(ICON_FILE_LINUX, f"{icon_dir}/{app_id}.png")

        if not flatpak:
            # Copy to root directory (AppImage spec)
            shutil.copy(ICON_FILE_LINUX, f"{appdir}/{app_id}.png")

    if flatpak:
        return

    # Download and run appimagetool to create the AppImage
    appimage_name = f"{APP_NAME}-{VERSION}-x86_64.AppImage"
    appimage_path = f"dist/{appimage_name}"

    print(f"\nDownloading appimagetool...")
    appimagetool_url = "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    appimagetool_path = "/tmp/appimagetool-x86_64.AppImage"

    # Download appimagetool if not already present
    if not os.path.exists(appimagetool_path):
        subprocess.run(["wget", "-q", "-O", appimagetool_path, appimagetool_url], check=True)
        os.chmod(appimagetool_path, 0o755)

    print(f"Creating {appimage_name}...")
    # Run appimagetool with --appimage-extract-and-run for CI environments without FUSE
    env = os.environ.copy()
    env["ARCH"] = "x86_64"  # Required by appimagetool
    subprocess.run([appimagetool_path, "--appimage-extract-and-run", appdir, appimage_path], env=env, check=True)

    print(f"[OK] Build complete: dist/{APP_NAME.lower()}")
    print(f"[OK] AppImage created: {appimage_path}")


def install_requirements():
    """Install required packages."""
    print("Installing requirements...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)


def main():
    """Main build function."""
    current_platform = get_platform()

    print(f"CrankBoy Manager Builder v{VERSION}")
    print(f"Platform: {current_platform}")
    print("=" * 50)

    # Parse arguments
    import argparse
    parser = argparse.ArgumentParser(description="Build CrankBoy Manager")
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts only")
    parser.add_argument("--install", action="store_true", help="Install requirements")
    parser.add_argument("--all", action="store_true", help="Build for all platforms (requires cross-compilation setup)")
    parser.add_argument("--flatpak", action="store_true", help="Build for Flatpak")
    args = parser.parse_args()

    if args.clean:
        clean_build()
        print("\n[OK] Clean complete")
        return

    if args.install:
        install_requirements()
        return

    # Clean previous builds
    clean_build()

    # Build for current platform
    try:
        if current_platform == "windows":
            build_windows()
        elif current_platform == "macos":
            build_macos()
        elif current_platform == "linux":
            build_linux(args.flatpak)
        else:
            print(f"Unsupported platform: {current_platform}")
            sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\n[FAIL] Build failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAIL] Build failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 50)
    print("[OK] Build complete!")
    print(f"Output: dist/")


if __name__ == "__main__":
    main()
