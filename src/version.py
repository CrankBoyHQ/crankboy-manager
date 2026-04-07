"""Version management for CrankBoy Manager.

This module provides the VERSION constant. It first tries to import
from a build-time generated module (_version_built.py), and falls back
to reading from the .version file for development.
"""

from pathlib import Path


def _read_version_file() -> str:
    """Read version from .version file (development mode).
    
    Tries multiple locations to find the .version file:
    1. Project root (for development)
    2. PyInstaller bundle root (for packaged app)
    3. Fallback to "1.0.0"
    """
    import sys
    
    # Possible locations for .version file
    possible_paths = [
        # Development: src/../.version (project root)
        Path(__file__).parent.parent / ".version",
        # PyInstaller onefile: sys._MEIPASS/.version
        Path(getattr(sys, '_MEIPASS', '')) / ".version" if hasattr(sys, '_MEIPASS') else None,
        # PyInstaller onedir: executable_dir/.version
        Path(sys.executable).parent / ".version" if hasattr(sys, 'executable') else None,
    ]
    
    for version_file in possible_paths:
        if version_file and version_file.exists():
            try:
                with open(version_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('version='):
                            return line.split('=', 1)[1].strip()
            except Exception:
                continue
    
    # Fallback version
    return "1.0.0"


# Try to import from build-time generated module first
try:
    from ._version_built import VERSION
except ImportError:
    # Fallback: read from .version file (development mode)
    VERSION = _read_version_file()

__version__ = VERSION


if __name__ == "__main__":
    print(f"CrankBoy Manager version: {VERSION}")
