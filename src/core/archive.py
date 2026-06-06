"""Helpers for pulling Game Boy ROMs out of common archive files.

Both the main ROM-upload flow (`FileListWidget`) and the launcher/forwarder
builder (`ForwarderDialog`) let the user drop or browse an archive instead of
a bare ROM. We only lean on the Python standard library, so the supported
formats are ZIP and the tar family (.tar, .tar.gz/.tgz, .tar.bz2/.tbz2,
.tar.xz/.txz).
"""

import os
import tarfile
import tempfile
import zipfile


# ROM file extensions we extract from archives.
ROM_EXTS = ('.gb', '.gbc', '.gbz')

# Archive extensions we know how to open with the stdlib.
_ZIP_EXTS = ('.zip',)
_TAR_EXTS = (
    '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tbz',
    '.tar.xz', '.txz',
)
ARCHIVE_EXTS = _ZIP_EXTS + _TAR_EXTS


class ArchiveError(Exception):
    """Raised when an archive can't be opened or read."""


def is_archive(path):
    """True if `path` looks like an archive we can open."""
    return path.lower().endswith(ARCHIVE_EXTS)


def _is_rom_member(name):
    """True if an archive member name is a ROM we should extract."""
    base = os.path.basename(name)
    # Skip directories and macOS resource-fork sidecar files (._foo).
    if not base or base.startswith('._'):
        return False
    return os.path.splitext(base)[1].lower() in ROM_EXTS


def _write_member(temp_dir, member_name, data, index):
    """Write extracted bytes to `temp_dir`, keyed by the member's basename.

    Only the basename is used (never the in-archive directory path), which
    sidesteps path-traversal ("zip slip") attacks. If two members share a
    basename, later ones get an index suffix so neither is clobbered.
    """
    base = os.path.basename(member_name)
    dest = os.path.join(temp_dir, base)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(base)
        dest = os.path.join(temp_dir, f"{stem}_{index}{ext}")
    with open(dest, 'wb') as fh:
        fh.write(data)
    return dest


def _extract_zip(path, temp_dir):
    out = []
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            for member in zf.namelist():
                if member.endswith('/') or not _is_rom_member(member):
                    continue
                out.append(_write_member(temp_dir, member, zf.read(member), len(out)))
    except zipfile.BadZipFile:
        raise ArchiveError(f"{os.path.basename(path)} is not a valid ZIP file")
    return out


def _extract_tar(path, temp_dir):
    out = []
    try:
        with tarfile.open(path, 'r:*') as tf:
            for member in tf.getmembers():
                if not member.isfile() or not _is_rom_member(member.name):
                    continue
                fh = tf.extractfile(member)
                if fh is None:
                    continue
                out.append(_write_member(temp_dir, member.name, fh.read(), len(out)))
    except tarfile.TarError:
        raise ArchiveError(f"{os.path.basename(path)} is not a valid tar archive")
    return out


def extract_roms(path):
    """Extract every Game Boy ROM (.gb/.gbc/.gbz) from the archive at `path`.

    Returns a list of filesystem paths to the extracted ROMs, written to a
    fresh temp directory. The list is empty if the archive holds no ROMs.

    Raises ArchiveError if the file isn't a recognised archive type or is
    corrupt/unreadable.
    """
    lower = path.lower()
    temp_dir = tempfile.mkdtemp(prefix='crankboy_archive_')
    try:
        if lower.endswith(_ZIP_EXTS):
            return _extract_zip(path, temp_dir)
        if lower.endswith(_TAR_EXTS):
            return _extract_tar(path, temp_dir)
    except ArchiveError:
        raise
    except Exception as e:
        raise ArchiveError(
            f"{os.path.basename(path)}: could not extract archive ({e})"
        )
    raise ArchiveError(f"{os.path.basename(path)} is not a supported archive")
