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
ICON_FILE = None  # Add path to .ico (Windows) or .icns (macOS) file


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
        "--clean",
    ]

    if ICON_FILE and os.path.exists(ICON_FILE):
        cmd.extend(["--icon", ICON_FILE])

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
        "--clean",
    ]

    if ICON_FILE and os.path.exists(ICON_FILE):
        cmd.extend(["--icon", ICON_FILE])

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


def build_linux():
    """Build Linux executable."""
    print("\n=== Building Linux executable ===")

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
        "--clean",
    ]

    if ICON_FILE and os.path.exists(ICON_FILE):
        cmd.extend(["--icon", ICON_FILE])

    cmd.append(MAIN_SCRIPT)

    subprocess.run(cmd, check=True)

    # Create tar.gz archive
    archive_name = f"{APP_NAME}-{VERSION}-linux"
    print(f"\nCreating {archive_name}.tar.gz...")

    # Create AppDir structure for AppImage (optional)
    appdir = f"dist/{APP_NAME}.AppDir"
    os.makedirs(appdir, exist_ok=True)
    os.makedirs(f"{appdir}/usr/bin", exist_ok=True)

    # Copy executable
    shutil.copy(f"dist/{APP_NAME.lower()}", f"{appdir}/usr/bin/")

    # Create AppRun script
    # APPDIR is provided by AppImage runtime
    apprun = """#!/bin/bash
exec "${{APPDIR}}/usr/bin/{app_name}" "$@"
""".format(app_name=APP_NAME.lower())
    with open(f"{appdir}/AppRun", "w") as f:
        f.write(apprun)
    os.chmod(f"{appdir}/AppRun", 0o755)

    # Create desktop entry
    desktop = f"""[Desktop Entry]
Name={APP_DISPLAY_NAME}
Exec={APP_NAME.lower()}
Icon={APP_NAME.lower()}
Type=Application
Categories=Utility;Game;
Comment=Transfer Game Boy ROMs to CrankBoy
"""
    with open(f"{appdir}/{APP_NAME.lower()}.desktop", "w") as f:
        f.write(desktop)

    # Create archive
    shutil.make_archive(
        f"dist/{archive_name}",
        'gztar',
        'dist',
        f"{APP_NAME}.AppDir"
    )

    print(f"[OK] Build complete: dist/{APP_NAME.lower()}")
    print(f"[OK] Archive created: dist/{archive_name}.tar.gz")


def install_requirements():
    """Install required packages."""
    print("Installing requirements...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)


def main():
    """Main build function."""
    current_platform = get_platform()

    print(f"CrankBoy Transfer GUI Builder v{VERSION}")
    print(f"Platform: {current_platform}")
    print("=" * 50)

    # Parse arguments
    import argparse
    parser = argparse.ArgumentParser(description="Build CrankBoy Transfer GUI")
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts only")
    parser.add_argument("--install", action="store_true", help="Install requirements")
    parser.add_argument("--all", action="store_true", help="Build for all platforms (requires cross-compilation setup)")
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
            build_linux()
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
