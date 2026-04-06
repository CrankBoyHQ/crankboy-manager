"""Core transfer engine for CrankBoy file transfers.

This module contains the core logic for transferring files to CrankBoy,
including GBZ compression and the ft protocol implementation.
"""

import os
import base64
import zlib
import gzip
import io
import urllib.parse

from src.core.database import database as rom_database


# Constants
FT_CHUNK_SIZE = 177  # 0xB1 - matches device chunk size
SERIAL_TIMEOUT = 5


def calculate_crc32(data):
    """Calculate CRC32 of data."""
    return zlib.crc32(data) & 0xFFFFFFFF


def compress_to_gbz(data, is_gbc=True):
    """
    Compress ROM data to GBZ format.
    
    GBZ format:
    [0:8]   Magic: "CB\x00\xFFGBgz" (8 bytes)
    [8]     Version: 1
    [9]     is_gbc: 1 if .gbc, 0 if .gb
    [10:14] CRC32 of original (big-endian)
    [14:18] Original size (little-endian uint32)
    [18:46] ROM header bytes 0x134-0x14F (28 bytes)
    [46:0x150] 0xFF padding
    [0x150:] gzip compressed data
    
    Returns: (gbz_data, original_crc, gbz_crc)
    """
    # Calculate CRC32 of original
    original_crc = zlib.crc32(data) & 0xFFFFFFFF
    
    # Extract ROM header bytes 0x134-0x14F (28 bytes)
    rom_header = data[0x134:0x150]
    
    # Compress data with gzip
    compressed = gzip.compress(data, compresslevel=9)
    
    # Build GBZ header
    gbz = bytearray()
    # Magic: "CB\x00\xFFGBgz" = 8 bytes
    gbz.extend(b'CB\x00\xFFGBgz')
    # Version
    gbz.append(1)
    # is_gbc
    gbz.append(1 if is_gbc else 0)
    # CRC32 (big-endian)
    gbz.extend(original_crc.to_bytes(4, 'big'))
    # Original size (little-endian uint32)
    gbz.extend(len(data).to_bytes(4, 'little'))
    # ROM header (28 bytes)
    gbz.extend(rom_header)
    # Padding 0xFF from current position to 0x150
    padding_needed = 0x150 - len(gbz)
    gbz.extend(b'\xFF' * padding_needed)
    # Compressed data
    gbz.extend(compressed)
    
    gbz_data = bytes(gbz)
    # Calculate CRC32 of the GBZ file itself (for transfer verification)
    gbz_crc = zlib.crc32(gbz_data) & 0xFFFFFFFF
    
    return gbz_data, original_crc, gbz_crc


def get_file_info(filepath, keep_compressed=False):
    """
    Get file information for transfer.
    
    Args:
        filepath: Path to the file
        keep_compressed: If True, don't provide original info so device keeps GBZ as-is
    
    Returns a dict with:
    - filepath: Original path
    - filename: Display name
    - is_user_gbz: Whether it's a pre-compressed GBZ
    - gbz_data: Data to transfer (compressed or as-is)
    - gbz_size: Size of data to transfer
    - gbz_crc: CRC of data to transfer
    - original_filename: Original name (for decompression, None if keep_compressed)
    - original_crc: Original CRC (for verification, None if keep_compressed)
    - original_size: Original uncompressed size
    """
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filepath)[1].lower()
    is_gbc = ext == '.gbc'
    
    info = {
        'filepath': filepath,
        'filename': filename,
    }
    
    if ext == '.gbz':
        # User-provided GBZ - transfer as-is
        with open(filepath, 'rb') as f:
            gbz_data = f.read()
        
        info.update({
            'is_user_gbz': True,
            'gbz_data': gbz_data,
            'gbz_size': len(gbz_data),
            'gbz_crc': calculate_crc32(gbz_data),
            'original_filename': None,
            'original_crc': None,
            'original_size': len(gbz_data),
        })
    else:
        # Compress .gb or .gbc
        with open(filepath, 'rb') as f:
            original_data = f.read()
        
        original_crc = calculate_crc32(original_data)
        gbz_data, _, gbz_crc = compress_to_gbz(original_data, is_gbc=is_gbc)
        
        base_name = os.path.splitext(filename)[0]
        gbz_filename = f"{base_name}.gbz"
        
        if keep_compressed:
            # Keep as GBZ on device - don't provide original info
            info.update({
                'is_user_gbz': False,
                'gbz_data': gbz_data,
                'gbz_size': len(gbz_data),
                'gbz_crc': gbz_crc,
                'gbz_filename': gbz_filename,
                'original_filename': None,
                'original_crc': None,
                'original_size': len(original_data),
            })
        else:
            # Decompress on device - provide original info
            info.update({
                'is_user_gbz': False,
                'gbz_data': gbz_data,
                'gbz_size': len(gbz_data),
                'gbz_crc': gbz_crc,
                'gbz_filename': gbz_filename,
                'original_filename': filename,
                'original_crc': original_crc,
                'original_size': len(original_data),
            })
    
    return info


def get_file_info_with_crc(filepath, keep_compressed=False):
    """
    Get file information for transfer, including CRC32 for cover lookup.

    This extends get_file_info() to also calculate the original CRC32 which
    is needed for cover art database lookups. Covers will be downloaded
    separately in the background.

    Args:
        filepath: Path to the file
        keep_compressed: If True, don't provide original info so device keeps GBZ as-is

    Returns a dict with:
    - All fields from get_file_info()
    - original_crc: The CRC32 of the original ROM (for database lookup)
    """
    # Get base file info
    info = get_file_info(filepath, keep_compressed)

    # Calculate original CRC32 for database lookup (if not already present)
    original_crc = info.get('original_crc')
    if original_crc is None:
        # For GBZ files without decompression info, we need to calculate from original
        ext = os.path.splitext(filepath)[1].lower()
        if ext != '.gbz':
            with open(filepath, 'rb') as f:
                original_data = f.read()
            original_crc = calculate_crc32(original_data)

    # Store CRC for later cover download
    info['original_crc'] = original_crc

    # Cover fields will be populated by CoverDownloadWorker
    info.setdefault('cover_data', None)
    info.setdefault('cover_filename', None)
    info.setdefault('cover_url', None)

    return info


def send_command(ser, cmd, verbose=False):
    """Send a command with 'msg' prefix."""
    full_cmd = f"msg {cmd}\n"
    ser.write(full_cmd.encode('utf-8'))


def read_response(ser, timeout=SERIAL_TIMEOUT, verbose=False, skip_echo=True):
    """Read a response line, filtering out non-protocol messages and echoes.
    
    Args:
        ser: Serial connection
        timeout: Maximum time to wait for response
        verbose: Whether to print debug info
        skip_echo: Whether to skip "msg " echo lines (default True)
    """
    import time
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            # Read with short timeout to allow filtering multiple lines
            ser.timeout = 0.1
            line = ser.readline()
            if not line:
                continue

            decoded = line.decode('utf-8', errors='ignore').strip()
            if not decoded:
                continue

            # Filter out debug/log messages that don't start with protocol prefixes
            if decoded.startswith('[SERIAL]') or decoded.startswith('log:'):
                continue

            # Skip echo lines (commands echoed back with "msg " prefix)
            if skip_echo and decoded.startswith('msg '):
                continue

            # Check if it's a valid protocol response
            if decoded.startswith(('ft:', 'cb:')):
                return decoded

        except Exception:
            continue

    return None  # Timeout


def parse_response(response):
    """Parse ft: or cb: protocol response."""
    if not response or not response.startswith(("ft:", "cb:")):
        return None, None, None
    
    parts = response.split(':', 2)
    if len(parts) < 2:
        return None, None, None
    
    proto = parts[0]
    cmd = parts[1]
    params = parts[2] if len(parts) > 2 else ""
    
    return proto, cmd, params
