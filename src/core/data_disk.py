"""Data-disk (USB Mass Storage) helpers for the Playdate.

When the Playdate enters data-disk mode it appears on the host as a
removable drive labeled "PLAYDATE". This module enters that mode, finds
the mount, and ejects.

Reference:
  https://github.com/cranksters/playdate-reverse-engineering/blob/main/usb/usb.md
"""

import os
import sys
import time
import subprocess
import shutil


PLAYDATE_VOLUME_LABEL = "PLAYDATE"


def enter_data_disk_mode(ser, log=None):
    """Send the data-disk command over serial.

    The serial connection is closed by the device when it switches modes,
    so we just write and don't wait for a response. Optional `log` is a
    callable that receives a single human-readable message.
    """
    try:
        ser.write(b"datadisk\n")
        ser.flush()
        if log:
            log("datadisk: wrote 'datadisk\\n' over serial")
    except Exception as e:
        # The port may already be closing as the device switches modes.
        if log:
            log(f"datadisk: serial write raised {e!r} (often expected)")


def _candidate_mount_dirs():
    """Return platform-appropriate base directories under which the
    PLAYDATE mount could appear.
    """
    if sys.platform == "darwin":
        return ["/Volumes"]
    if sys.platform.startswith("linux"):
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        bases = []
        if user:
            bases.append(f"/run/media/{user}")
            bases.append(f"/media/{user}")
        bases.append("/media")
        bases.append("/mnt")
        return [b for b in bases if os.path.isdir(b)]
    return []


def find_mount(timeout=20.0, log=None):
    """Poll for the PLAYDATE volume mount and return its absolute path.

    Returns None on timeout. Optional `log` is a callable that receives a
    single human-readable message; emitted at most once per second to
    avoid spamming.
    """
    bases = [] if sys.platform == "win32" else _candidate_mount_dirs()
    if log:
        if sys.platform == "win32":
            log("find_mount: scanning Windows drive letters by volume label")
        elif bases:
            log("find_mount: scanning " + ", ".join(bases))
        else:
            log("find_mount: no candidate mount directories on this platform!")

    deadline = time.time() + timeout
    last_log = 0.0
    while time.time() < deadline:
        if sys.platform == "win32":
            path = _find_mount_windows()
            if path:
                if log:
                    log(f"find_mount: found {path}")
                return path
        else:
            for base in bases:
                candidate = os.path.join(base, PLAYDATE_VOLUME_LABEL)
                if os.path.isdir(candidate):
                    if log:
                        log(f"find_mount: found {candidate}")
                    return candidate
        if log and (time.time() - last_log) >= 1.0:
            last_log = time.time()
            elapsed = time.time() - (deadline - timeout)
            log(f"find_mount: still waiting... ({elapsed:.0f}s elapsed)")
        time.sleep(0.4)
    if log:
        log(f"find_mount: timed out after {timeout:.0f}s")
    return None


def _find_mount_windows():
    """Scan Windows drive letters for one labeled PLAYDATE."""
    try:
        import string
        import ctypes
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(64)
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if not os.path.isdir(root):
                continue
            if kernel32.GetVolumeInformationW(
                root, buf, ctypes.sizeof(buf), None, None, None, None, 0
            ):
                if buf.value.strip() == PLAYDATE_VOLUME_LABEL:
                    return root
    except Exception:
        return None
    return None


def eject(mount_path, log=None):
    """Eject the PLAYDATE volume. Returns True on success.

    On Linux this uses `gio mount --eject`, matching ~/.scripts/pd-install.
    `gio mount --eject` performs an unmount followed by a SCSI EJECT which
    is what makes the Playdate firmware actually leave data-disk mode (a
    plain unmount or `udisksctl power-off` leaves it stuck on "Eject disk
    to reboot"). The first attempt sometimes returns non-zero while the
    device is still busy, so retry briefly.
    """
    if not mount_path:
        return False

    def _log(msg):
        if log:
            log(msg)

    if sys.platform == "darwin":
        # diskutil can transiently fail right after a copy while the
        # OS finishes flushing; retry the same way the Linux path does.
        for attempt in range(1, 11):
            _log(f"eject: diskutil eject {mount_path} (attempt {attempt})")
            if _run(["diskutil", "eject", mount_path]):
                return True
            time.sleep(1)
        _log("eject: diskutil eject failed after 10 attempts")
        return False

    if sys.platform.startswith("linux"):
        # Inside a Flatpak sandbox, `gio` ships in the runtime but it
        # talks to the user-session gvfs daemon, which the sandbox
        # can't reach. Delegate to the host's gio via flatpak-spawn.
        # Requires --talk-name=org.freedesktop.Flatpak in finish-args.
        in_flatpak = os.path.exists("/.flatpak-info")
        if in_flatpak:
            cmd_prefix = ["flatpak-spawn", "--host"]
        else:
            cmd_prefix = []
            if not shutil.which("gio"):
                _log("eject: 'gio' not found; install glib2")
                return False
        for attempt in range(1, 11):
            cmd = cmd_prefix + ["gio", "mount", "--eject", mount_path]
            _log(f"eject: {' '.join(cmd)} (attempt {attempt})")
            if _run(cmd):
                return True
            time.sleep(1)
        _log("eject: gio mount --eject failed after 10 attempts")
        return False

    if sys.platform == "win32":
        _log(f"eject: Windows COM eject for {mount_path}")
        return _eject_windows(mount_path)

    _log(f"eject: unsupported platform {sys.platform!r}")
    return False


def _run(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=20)
        return result.returncode == 0
    except Exception:
        return False


def _resolve_device(mount_path):
    """On Linux, look up the underlying block device for a mount path."""
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", mount_path],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        pass
    return None


def _eject_windows(mount_path):
    try:
        letter = os.path.splitdrive(mount_path)[0]
        if not letter:
            return False
        ps = (
            "$sh = New-Object -comObject Shell.Application;"
            f"$drive = $sh.Namespace(17).ParseName('{letter}');"
            "if ($drive) {{ $drive.InvokeVerb('Eject'); exit 0 }} else {{ exit 1 }}"
        )
        return _run(["powershell", "-NoProfile", "-Command", ps])
    except Exception:
        return False
