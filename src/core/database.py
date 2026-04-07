"""Database module for looking up ROM information by CRC32."""

import os
import sys
import json
import gzip
import zlib
from pathlib import Path


def _get_db_dir() -> Path:
    """Get the database directory path.
    
    Handles both development and PyInstaller bundle modes.
    """
    # Check if running as PyInstaller bundle
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller: db is at bundle root
        return Path(sys._MEIPASS) / "db"
    else:
        # Development: db is at project root
        return Path(__file__).parent.parent.parent / "db"


# Database configuration
DB_MASK = 0xFC  # Should match create_rom_list.py
DB_DIR = _get_db_dir()

# Cover download configuration
COVERS_BASE_URL = "https://raw.githubusercontent.com/CrankBoyHQ/crankboy-covers/refs/heads/main/Combined_Boxarts"


class RomDatabase:
    """Database for ROM information lookups."""

    def __init__(self):
        self._cache = {}  # Cache loaded DB files

    def _get_db_filename(self, crc32: int) -> str:
        """Get the database filename for a given CRC32.

        Files are named by the first 2 hex chars of (crc32 >> 24) & 0xFC
        """
        prefix = (crc32 >> 24) & DB_MASK
        return f"{prefix:02x}.json.gz"

    def _load_db_file(self, filename: str) -> dict:
        """Load and parse a database file."""
        if filename in self._cache:
            return self._cache[filename]

        filepath = DB_DIR / filename
        if not filepath.exists():
            return {}

        try:
            with gzip.open(filepath, 'rt', encoding='utf-8') as f:
                data = json.load(f)
                self._cache[filename] = data
                return data
        except (gzip.BadGzipFile, json.JSONDecodeError, IOError):
            return {}

    def lookup(self, crc32: int) -> dict | None:
        """Look up ROM information by CRC32.

        Args:
            crc32: The CRC32 of the ROM

        Returns:
            Dict with 'long' and 'short' names, or None if not found
        """
        crc_hex = f"{crc32:08X}"
        db_filename = self._get_db_filename(crc32)
        db_data = self._load_db_file(db_filename)

        return db_data.get(crc_hex)

    def get_cover_filename(self, crc32: int) -> str | None:
        """Get the cover art filename for a ROM by CRC32.

        Args:
            crc32: The CRC32 of the ROM

        Returns:
            The encoded cover filename (without extension), or None if not found
        """
        rom_info = self.lookup(crc32)
        if not rom_info:
            return None

        long_name = rom_info.get('long')
        if not long_name:
            return None

        return encode_cover_filename(long_name)

    def get_cover_url(self, crc32: int) -> str | None:
        """Get the full URL for cover art download.

        Args:
            crc32: The CRC32 of the ROM

        Returns:
            The full URL to download the cover, or None if not found
        """
        cover_filename = self.get_cover_filename(crc32)
        if not cover_filename:
            return None

        return f"{COVERS_BASE_URL}/{cover_filename}.pdi"

    def get_cover_info(self, crc32: int) -> dict | None:
        """Get cover information for a ROM by CRC32.

        Args:
            crc32: The CRC32 of the ROM

        Returns:
            Dict with 'url' and 'filename' keys, or None if not found
        """
        cover_filename = self.get_cover_filename(crc32)
        if not cover_filename:
            return None

        cover_url = self.get_cover_url(crc32)
        if not cover_url:
            return None

        return {
            'url': cover_url,
            'filename': f"{cover_filename}.pdi"
        }


def encode_cover_filename(name: str) -> str:
    """Encode a ROM name for use as a cover filename.

    This matches the encoding used in CrankBoy:
    - Spaces -> %20
    - & -> _
    - : -> _
    - é (0xC3 0xA9) -> e

    Args:
        name: The ROM name from the database

    Returns:
        The encoded filename
    """
    result = []
    i = 0
    while i < len(name):
        char = name[i]

        # Handle UTF-8 é (0xC3 0xA9)
        if (i + 1 < len(name) and
            ord(char) == 0xC3 and
            ord(name[i + 1]) == 0xA9):
            result.append('e')
            i += 2
            continue

        # Handle special characters
        if char == ' ':
            result.append('%20')
        elif char in '&:':
            result.append('_')
        else:
            result.append(char)

        i += 1

    return ''.join(result)


# Global database instance
database = RomDatabase()
