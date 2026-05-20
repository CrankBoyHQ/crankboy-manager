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


def get_rom_cgb_support(filepath):
    """
    Read ROM header to determine CGB (Color Game Boy) support.
    
    Checks byte at offset 0x143 in the ROM header:
    - 0x80: Supports both DMG (original GB) and CGB (Color)
    - 0xC0: CGB only (Color only)
    - Other: DMG only (original Game Boy)
    
    Returns True if ROM supports CGB mode.
    """
    try:
        with open(filepath, 'rb') as f:
            f.seek(0x143)
            cgb_byte = f.read(1)
            if len(cgb_byte) == 0:
                return False
            cgb_byte = cgb_byte[0]
            # 0x80 = DMG+CGB, 0xC0 = CGB only
            return cgb_byte in (0x80, 0xC0)
    except (IOError, OSError):
        return False


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
    is_gbc = get_rom_cgb_support(filepath) if ext in ('.gb', '.gbc') else False
    
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


def read_response(ser, timeout=SERIAL_TIMEOUT, verbose=False, skip_echo=True,
                  on_line=None):
    """Read a response line, filtering out non-protocol messages and echoes.

    Args:
        ser: Serial connection
        timeout: Maximum time to wait for response
        verbose: Whether to print debug info
        skip_echo: Whether to skip "msg " echo lines (default True)
        on_line: Optional callback invoked once per accepted protocol
            line (e.g. "cb:fwdinstall:ok:..."). Receives the decoded
            line as its only argument. Used by the UI to log raw
            device responses in a distinct colour.
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
                if on_line is not None:
                    try:
                        on_line(decoded)
                    except Exception:
                        pass
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


# ---- forwarder serial helpers ----

def cb_pdxpath(ser, timeout=2.0, on_line=None):
    """Ask CrankBoy for its mounted .pdx path.

    Returns the (URL-decoded) path string, or None on error / unsupported.
    `on_line`, when provided, is invoked once per raw device response line.
    """
    send_command(ser, "cb:pdxpath")
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        line = read_response(ser, timeout=0.5, on_line=on_line)
        if not line or not line.startswith("cb:pdxpath:"):
            continue
        rest = line[len("cb:pdxpath:"):]
        if rest.startswith("ok:"):
            return urllib.parse.unquote(rest[len("ok:"):])
        if rest.startswith("error:"):
            return None
    return None


def cb_pdxinfo(ser, timeout=2.0, on_line=None):
    """Ask CrankBoy for its pdxinfo. Returns a dict of pdxinfo fields,
    or None if it could not be fetched.
    """
    send_command(ser, "cb:pdxinfo")
    import time as _t
    deadline = _t.time() + timeout
    out = {}
    saw_any = False
    while _t.time() < deadline:
        line = read_response(ser, timeout=0.5, on_line=on_line)
        if not line or not line.startswith("cb:pdxinfo:"):
            continue
        rest = line[len("cb:pdxinfo:"):]
        if rest == "end":
            return out if saw_any else None
        if rest.startswith("error:"):
            return None
        if rest.startswith("kv:"):
            payload = rest[len("kv:"):]
            # payload = <urlenc-key>:<urlenc-value>; key has no ':' after decoding
            # but the encoded form may also be free of ':' since url_encode escapes it.
            if ":" not in payload:
                continue
            enc_k, enc_v = payload.split(":", 1)
            k = urllib.parse.unquote(enc_k)
            v = urllib.parse.unquote(enc_v)
            out[k] = v
            saw_any = True
    return None


def send_bitmap(ser, fb_bytes):
    """Push a Playdate framebuffer (12000 raw bytes) over serial via the
    `bitmap` command.

    Per playdate-reverse-engineering/usb.md, the payload is the tightly
    packed 400x240 1-bit framebuffer: 50 bytes per row (400 bits
    MSB-first), 240 rows, no padding -- 12000 bytes total. 1 = white,
    0 = black. The firmware does not ack; we just write + flush.
    """
    if len(fb_bytes) != 50 * 240:
        raise ValueError(
            f"framebuffer must be {50*240} bytes, got {len(fb_bytes)}"
        )
    try:
        ser.write(b"bitmap\n")
        ser.write(fb_bytes)
        ser.flush()
    except Exception:
        pass


def launch_pdx_path(ser, pdx_path):
    """Send the Playdate firmware's `run <path>` serial command to launch
    a .pdx by absolute device-side path (e.g. /Games/CrankBoy.pdx).

    The device doesn't ack; we just write and flush.
    """
    cmd = f"run {pdx_path}\n"
    try:
        ser.write(cmd.encode("utf-8"))
        ser.flush()
    except Exception:
        pass


def cb_fwdinstall(ser, timeout=15.0, on_line=None):
    """Tell CrankBoy to install/refresh the shared forwarder. Returns the
    install dir path (e.g. "/Shared/.forwarder/<bundleID>") on success,
    or None on failure / unsupported.
    """
    send_command(ser, "cb:fwdinstall")
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        line = read_response(ser, timeout=1.0, on_line=on_line)
        if not line or not line.startswith("cb:fwdinstall:"):
            continue
        rest = line[len("cb:fwdinstall:"):]
        if rest.startswith("ok:"):
            return urllib.parse.unquote(rest[len("ok:"):])
        if rest.startswith("error:"):
            return None
    return None


def parse_version_tuple(version_str):
    """Best-effort parse of a "vX.Y.Z" or "X.Y.Z" string into a tuple. Any
    non-numeric prefix is stripped, suffixes ignored. Missing parts default
    to 0. Returns None if parsing fails entirely.
    """
    if not version_str:
        return None
    s = version_str.strip()
    if s.startswith("v") or s.startswith("V"):
        s = s[1:]
    # Cut on first non-version char (whitespace, '-', '+', etc.)
    for i, ch in enumerate(s):
        if not (ch.isdigit() or ch == '.'):
            s = s[:i]
            break
    parts = s.split('.')
    try:
        nums = [int(p) for p in parts if p != ""]
    except ValueError:
        return None
    if not nums:
        return None
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def version_at_least(version_str, minimum=(2, 0, 3)):
    """True if the parsed version string is >= minimum tuple."""
    v = parse_version_tuple(version_str)
    if v is None:
        return False
    return v >= tuple(minimum)
