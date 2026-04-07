"""Cover art downloader module."""

import os
import sys
import urllib.request
import urllib.error
import ssl
import time
from pathlib import Path
from typing import Callable

# Try importing certifi for SSL certificates
try:
    import certifi
    HAS_CERTIFI = True
except ImportError:
    HAS_CERTIFI = False

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.database import database
from src.version import VERSION

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # seconds

# User-Agent with dynamic version
USER_AGENT = f'CrankBoyManager/{VERSION}'


def get_ssl_context():
    """Create an SSL context using certifi if available."""
    try:
        if HAS_CERTIFI:
            return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    
    return ssl.create_default_context()


def download_cover(crc32: int, progress_callback: Callable[[int, int], None] | None = None) -> bytes | None:
    """Download cover art for a ROM by CRC32.

    Returns:
        The cover art data as bytes, or None if download fails.
    """
    # Get cover URL from database
    cover_url = database.get_cover_url(crc32)
    if not cover_url:
        return None

    # Try downloading with retries
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = _download_url(cover_url, progress_callback)
            if data:
                return data
        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    return None


def _download_url(url: str, progress_callback: Callable[[int, int], None] | None = None) -> bytes:
    """Download data from a URL."""
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': '*/*'
    }

    req = urllib.request.Request(url, headers=headers)
    context = get_ssl_context()

    with urllib.request.urlopen(req, timeout=30, context=context) as response:
        total_size = int(response.info().get('Content-Length', 0))
        data = response.read()

        if progress_callback and total_size > 0:
            progress_callback(len(data), total_size)

        return data
