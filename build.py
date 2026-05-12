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
from datetime import datetime
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


def build_macos():
    """Build macOS app bundle."""
    print("\n=== Building macOS app bundle ===")

    # Generate version module with hardcoded version
    generate_version_module()

    cmd = [
        "pyinstaller",
        "--onedir",  # Use onedir mode for macOS .app bundles
        "--windowed",
        "--name", APP_DISPLAY_NAME,
        "--add-data", "src:src",
        "--add-data", "db:db",
        "--hidden-import", "serial",
        "--hidden-import", "serial.tools.list_ports",
        "--hidden-import", "certifi",
        "--clean",
    ]

    if ICON_FILE_MACOS and os.path.exists(ICON_FILE_MACOS):
        cmd.extend(["--icon", ICON_FILE_MACOS])

    cmd.append(MAIN_SCRIPT)

    subprocess.run(cmd, check=True)

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

    if not flatpak:
        # Generate version module with hardcoded version
        generate_version_module()

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

    # Create AppStream metadata
    os.makedirs(f"{appdir}/usr/share/metainfo", exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    appstream_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>{app_id}</id>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>MIT</project_license>
  <name>{APP_DISPLAY_NAME}</name>
  <summary>Transfer Game Boy ROMs to CrankBoy</summary>
  <description>
    <p>CrankBoy Manager is a desktop application for transferring Game Boy ROMs to your Playdate device running CrankBoy.</p>
    <p>Features:</p>
    <ul>
      <li>Drag and drop ROM files</li>
      <li>Support for .gb, .gbc, and .gbz files</li>
      <li>ZIP archive support</li>
      <li>Automatic cover art download</li>
      <li>Batch transfer multiple ROMs</li>
    </ul>
  </description>
  <launchable type="desktop-id">{app_id}.desktop</launchable>
  <url type="homepage">https://crankboy.app</url>
  <url type="vcs-browser">https://github.com/CrankBoyHQ/crankboy-manager</url>
  <developer_name>CrankBoy Dev Team</developer_name>
  <content_rating type="oars-1.1"/>
  <screenshots>
    <screenshot type="default">
      <caption>The main window</caption>
      <image>https://raw.githubusercontent.com/CrankBoyHQ/crankboy-manager/main/screenshot.png</image>
    </screenshot>
  </screenshots>
  <releases>
    <release version="{VERSION}" date="{date_str}"/>
  </releases>
</component>
"""
    with open(f"{appdir}/usr/share/metainfo/{app_id}.appdata.xml", "w") as f:
        f.write(appstream_xml)

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
