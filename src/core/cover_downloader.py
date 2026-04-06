"""Cover art downloader module."""

import urllib.request
import urllib.error
import time
from typing import Callable

from src.core.database import database

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # seconds


def download_cover(crc32: int, progress_callback: Callable[[int, int], None] | None = None) -> bytes | None:
    """Download cover art for a ROM by CRC32.

    This function will retry up to MAX_RETRIES times if the download fails.

    Args:
        crc32: The CRC32 of the ROM
        progress_callback: Optional callback(current_bytes, total_bytes) for progress

    Returns:
        The cover art data as bytes, or None if:
        - No cover found in database for this CRC32
        - Download failed after all retries
    """
    # Get cover URL from database
    cover_url = database.get_cover_url(crc32)
    if not cover_url:
        return None  # No cover in database

    # Try downloading with retries
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = _download_url(cover_url, progress_callback)
            return data
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            continue

    # All retries failed
    return None  # Don't raise error, just return None


def _download_url(url: str, progress_callback: Callable[[int, int], None] | None = None) -> bytes:
    """Download data from a URL.

    Args:
        url: The URL to download from
        progress_callback: Optional callback(current_bytes, total_bytes)

    Returns:
        The downloaded data as bytes
    """
    headers = {
        'User-Agent': 'CrankBoyTransfer/1.0.0'
    }

    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=30) as response:
        total_size = int(response.headers.get('Content-Length', 0))
        data = response.read()

        if progress_callback and total_size > 0:
            progress_callback(len(data), total_size)

        return data
