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


def find_mount(timeout=120.0, log=None):
    """Poll for the PLAYDATE volume and return its absolute path.

    On macOS and Linux this waits until the ''Games'' subdirectory
    inside the volume is visible — confirming the FAT32 filesystem has
    been fully attached, not just the mount-point directory created.
    This eliminates the need for a separate readiness check.

    Returns None on timeout.  Optional *log* is a callable receiving a
    single human-readable message; emitted at most once every 5 s to
    avoid spamming.
    """
    bases = [] if sys.platform == "win32" else _candidate_mount_dirs()
    if log:
        if sys.platform == "win32":
            log("scanning Windows drive letters for PLAYDATE...")
        elif bases:
            log("scanning " + ", ".join(bases) + " for PLAYDATE...")
        else:
            log("no candidate mount directories on this platform!")

    deadline = time.time() + timeout
    last_log = 0.0
    while time.time() < deadline:
        if sys.platform == "win32":
            path = _find_mount_windows()
            if path and os.path.isdir(os.path.join(path, "Games")):
                if log:
                    log(f"found {path} (ready)")
                return path
        else:
            for base in bases:
                candidate = os.path.join(base, PLAYDATE_VOLUME_LABEL)
                games_dir = os.path.join(candidate, "Games")
                if os.path.isdir(games_dir):
                    if log:
                        log(f"found {candidate} (ready)")
                    return candidate
        now = time.time()
        if log and (now - last_log) >= 5.0:
            last_log = now
            elapsed = now - (deadline - timeout)
            log(f"still waiting... ({elapsed:.0f}s elapsed)")
        time.sleep(0.5)
    if log:
        log(f"timed out after {timeout:.0f}s")
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


class EjectResult:
    """Outcome of an eject attempt.

    Truthy when the eject succeeded. On failure, `manual_prompt` carries a
    user-facing message the UI can show in a modal telling the user to
    eject the device by hand.
    """

    def __init__(self, ok, manual_prompt=None):
        self.ok = bool(ok)
        self.manual_prompt = manual_prompt

    def __bool__(self):
        return self.ok

    def __repr__(self):
        return f"EjectResult(ok={self.ok})"


def _in_flatpak():
    return os.path.exists("/.flatpak-info")


def manual_eject_message():
    """User-facing instructions shown when automatic eject fails."""
    msg = (
        "CrankBoy couldn't eject the Playdate automatically.\n\n"
        "Please eject the \"PLAYDATE\" volume manually (e.g. from your file "
        "manager) before disconnecting, so the device leaves data-disk mode."
    )
    if (
        sys.platform.startswith("linux")
        and not _in_flatpak()
        and not shutil.which("gio")
    ):
        msg += (
            "\n\nTip: installing GLib's 'gio' tool (the 'glib2' / "
            "'glib2-bin' package) lets CrankBoy eject automatically."
        )
    return msg


def eject(mount_path, log=None):
    """Eject the PLAYDATE volume. Returns an `EjectResult`.

    The eject must perform an unmount followed by a SCSI EJECT, which is
    what makes the Playdate firmware actually leave data-disk mode (a plain
    unmount or `udisksctl power-off` leaves it stuck on "Eject disk to
    reboot"). On Linux we do this via UDisks2 over the system D-Bus
    (Filesystem.Unmount + Drive.Eject) -- this works inside the Flatpak
    sandbox using only --system-talk-name=org.freedesktop.UDisks2, with no
    host access. Outside the sandbox we fall back to `gio mount --eject`
    (which drives the same operation through gvfs) if UDisks2 fails. The
    device is often briefly busy right after a copy, so each method retries.
    """
    if not mount_path:
        return EjectResult(False, manual_eject_message())

    def _log(msg):
        if log:
            log(msg)

    if sys.platform == "darwin":
        # diskutil can transiently fail right after a copy while the
        # OS finishes flushing; retry the same way the Linux path does.
        for attempt in range(1, 11):
            _log(f"eject: diskutil eject {mount_path} (attempt {attempt})")
            if _run(["diskutil", "eject", mount_path]):
                return EjectResult(True)
            time.sleep(1)
        _log("eject: diskutil eject failed after 10 attempts")
        return EjectResult(False, manual_eject_message())

    if sys.platform.startswith("linux"):
        device = _resolve_device(mount_path)
        if device:
            _log(f"eject: resolved {mount_path} -> {device}")
        else:
            _log(f"eject: could not resolve block device for {mount_path}")

        # 1. UDisks2 over D-Bus. This is the only path available inside the
        #    Flatpak sandbox, and the preferred one everywhere.
        if device:
            for attempt in range(1, 11):
                _log(f"eject: UDisks2 unmount+eject {device} (attempt {attempt})")
                if _udisks_eject_linux(device, _log):
                    return EjectResult(True)
                time.sleep(1)
            _log("eject: UDisks2 eject failed after 10 attempts")

        # 2. Outside the sandbox, fall back to host gio if available.
        if not _in_flatpak():
            if shutil.which("gio"):
                for attempt in range(1, 11):
                    cmd = ["gio", "mount", "--eject", mount_path]
                    _log(f"eject: {' '.join(cmd)} (attempt {attempt})")
                    if _run(cmd):
                        return EjectResult(True)
                    time.sleep(1)
                _log("eject: gio mount --eject failed after 10 attempts")
            else:
                _log("eject: 'gio' not found; no fallback available")

        return EjectResult(False, manual_eject_message())

    if sys.platform == "win32":
        _log(f"eject: Windows COM eject for {mount_path}")
        if _eject_windows(mount_path):
            return EjectResult(True)
        return EjectResult(False, manual_eject_message())

    _log(f"eject: unsupported platform {sys.platform!r}")
    return EjectResult(False, manual_eject_message())


def _udisks_eject_linux(device, log):
    """Unmount and SCSI-eject `device` (e.g. /dev/sda1) via UDisks2 on the
    system bus. Uses QtDBus, which is present both in the Qt runtime (inside
    the Flatpak sandbox) and via the PyQt6 dependency natively. Returns True
    on success.
    """
    try:
        from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
    except Exception as e:  # pragma: no cover - Qt should always be present
        log(f"eject: QtDBus unavailable ({e!r})")
        return False

    UD = "org.freedesktop.UDisks2"
    bus = QDBusConnection.systemBus()
    if not bus.isConnected():
        log("eject: system D-Bus not connected")
        return False

    name = os.path.basename(device)
    block_path = "/org/freedesktop/UDisks2/block_devices/" + name

    def _is_error(reply):
        return reply.type() == QDBusMessage.MessageType.ErrorMessage

    # Find the drive object that owns this block device.
    props = QDBusInterface(UD, block_path, "org.freedesktop.DBus.Properties", bus)
    if not props.isValid():
        log(f"eject: no UDisks2 block object for {name}")
        return False
    drive_reply = props.call("Get", UD + ".Block", "Drive")
    if _is_error(drive_reply):
        log(f"eject: read Drive prop -> {drive_reply.errorName()}")
        return False
    drive_path = _dbus_object_path(drive_reply)
    if not drive_path or drive_path == "/":
        log("eject: block device has no associated drive")
        return False

    # Unmount the filesystem first. A "not mounted" error here is fine; the
    # eject below is what matters, so only log and continue.
    fs = QDBusInterface(UD, block_path, UD + ".Filesystem", bus)
    if fs.isValid():
        r = fs.call("Unmount", {})
        if _is_error(r):
            log(f"eject: Unmount -> {r.errorName()}: {r.errorMessage()}")

    # SCSI eject -- this is what makes the Playdate leave data-disk mode.
    drive = QDBusInterface(UD, drive_path, UD + ".Drive", bus)
    if not drive.isValid():
        log("eject: UDisks2 Drive interface invalid")
        return False
    r = drive.call("Eject", {})
    if _is_error(r):
        log(f"eject: Drive.Eject -> {r.errorName()}: {r.errorMessage()}")
        return False
    return True


def _dbus_object_path(reply):
    """Extract an object-path string from a QDBusMessage reply, unwrapping a
    variant/QDBusObjectPath as needed. Returns None on failure.
    """
    try:
        from PyQt6.QtDBus import QDBusVariant, QDBusObjectPath
        args = reply.arguments()
        if not args:
            return None
        val = args[0]
        if isinstance(val, QDBusVariant):
            val = val.variant()
        if isinstance(val, QDBusObjectPath):
            return val.path()
        return str(val)
    except Exception:
        return None


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
