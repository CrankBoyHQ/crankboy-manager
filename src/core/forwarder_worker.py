"""Worker that builds and installs CrankBoy forwarders.

Workflow per ROM (see plan):
  1. Open serial; verify CrankBoy version >= 2.0.3.
  2. cb:pdxpath, cb:pdxinfo.
  3. If sharing crankboy.bin: cb:fwdinstall -> capture install dir.
  4. Close serial; enter data-disk mode; wait for the PLAYDATE mount.
  5. (If not sharing crankboy.bin) read crankboy.bin off the mount.
  6. Build the forwarder .pdx in a temp dir.
  7. Copy it into <mount>/Games/.
  8. Eject.
"""

import os
import shutil
import tempfile
import time

import serial
from PyQt6.QtCore import QThread, pyqtSignal

from src.core import data_disk
from src.core.forwarder_builder import build_forwarder_pdx
from src.core.transfer_engine import (
    cb_fwdinstall,
    cb_pdxinfo,
    cb_pdxpath,
    version_at_least,
)


MIN_CRANKBOY_VERSION = (2, 0, 3)


class LauncherCardWorker(QThread):
    """Off-UI worker that computes the launcher card (and possibly icon)
    for a ROM.

    Runs two lookups in parallel:
      - The crankboy-bundles manifest: if a maintainer-curated bundle
        exists for this title, its card.png + icon.png override
        everything else.
      - The normal `compose_launcher_card` chain (libretro -> CrankBoy
        covers -> text fallback).

    Emits `card_ready(rom_path, card_image, icon_image)`:
      - `card_image` is the final launcher card to use (bundle override
        wins; otherwise the composed card; None on failure).
      - `icon_image` is non-None only when a bundle override applies.
        The dialog uses it to replace the locally-generated icon.

    The dialog correlates by rom_path so a stale finish for a
    previously selected ROM can be ignored.
    """

    # (rom_path, card_image_or_none, icon_image_or_none)
    card_ready = pyqtSignal(str, object, object)

    def __init__(self, rom_path, title, rom_info, download_art=True, parent=None):
        super().__init__(parent)
        self.rom_path = rom_path
        self.title = title
        self.rom_info = rom_info
        self.download_art = download_art

    def run(self):
        from concurrent.futures import ThreadPoolExecutor
        from src.core.icon_builder import (
            compose_launcher_card,
            fetch_bundle_override,
        )

        if not self.download_art:
            # No network lookups: compose the text-only card locally (passing
            # rom_info=None skips the libretro/cover fetch) and keep the
            # locally-generated icon (icon_img=None).
            try:
                card_img = compose_launcher_card(title=self.title, rom_info=None)
            except Exception:
                card_img = None
            self.card_ready.emit(self.rom_path, card_img, None)
            return

        # Kick both lookups off in parallel. Each one is bounded by the
        # ~5s HTTP timeout inside _http_fetch.
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_bundle = pool.submit(fetch_bundle_override, self.rom_info)
            f_card = pool.submit(
                compose_launcher_card,
                title=self.title, rom_info=self.rom_info,
            )
            try:
                bundle = f_bundle.result()
            except Exception:
                bundle = None
            try:
                composed = f_card.result()
            except Exception:
                composed = None

        if bundle is not None:
            # Maintainer-curated override -> use both images directly.
            card_img, icon_img = bundle
        else:
            card_img = composed
            icon_img = None

        self.card_ready.emit(self.rom_path, card_img, icon_img)


def _crankboy_bin_candidates(mount_path, crankboy_pdx_path, crankboy_bundle_id):
    """Ordered list of candidate paths for crankboy.bin on the data disk."""
    candidates = []
    if crankboy_bundle_id:
        candidates.append(
            os.path.join(mount_path, "Data", crankboy_bundle_id, "crankboy.bin")
        )
    if crankboy_pdx_path:
        relative = crankboy_pdx_path.lstrip("/")
        candidates.append(os.path.join(mount_path, relative, "crankboy.bin"))
        candidates.append(os.path.join(mount_path, relative, "pdex.bin"))
    return candidates


def _crankboy_bin_on_disk(mount_path, crankboy_pdx_path, crankboy_bundle_id):
    """Locate crankboy.bin on a mounted PLAYDATE data disk.

    Returns the filesystem path or None.
    """
    for c in _crankboy_bin_candidates(
        mount_path, crankboy_pdx_path, crankboy_bundle_id
    ):
        if os.path.isfile(c):
            return c
    return None


class ForwarderWorker(QThread):
    """Builds and installs CrankBoy forwarders for a list of ROMs."""

    progress = pyqtSignal(str)             # human-readable status line
    log_message = pyqtSignal(str)
    # Raw protocol line received from the Playdate over serial; emitted
    # by `read_response` via the on_line hook. The dialog renders these
    # in a distinct grey colour so they stand out from manager-emitted
    # progress lines.
    device_log = pyqtSignal(str)
    rom_completed = pyqtSignal(str, bool, str)   # rom_path, success, message
    forwarder_installed = pyqtSignal(str, str)   # rom_path, device-side pdx path (/Games/<dir>.pdx)
    all_completed = pyqtSignal(bool)             # overall_success

    def __init__(self, port, rom_paths, options):
        """
        Args:
            port: Serial port device path.
            rom_paths: list of ROM file paths to wrap.
            options: dict with:
                share_crankboy_bin (bool)
                share_rom (bool)
                db_titles (dict[rom_path] -> str), optional
        """
        super().__init__()
        self.port = port
        self.rom_paths = list(rom_paths)
        self.options = options or {}
        self._is_running = True

    def stop(self):
        self._is_running = False

    def _emit(self, msg):
        self.progress.emit(msg)
        self.log_message.emit(msg)

    def run(self):
        share_crankboy_bin = bool(self.options.get("share_crankboy_bin", True))
        share_rom = bool(self.options.get("share_rom", True))
        download_art = bool(self.options.get("download_art", True))
        db_titles = self.options.get("db_titles", {}) or {}

        ser = None
        pdxpath = None
        pdxinfo = None
        fwd_install_path = None
        crankboy_bundle_id = None
        mount = None

        try:
            self._emit("=== Forwarder build started ===")
            self._emit(
                f"Options: share_crankboy_bin={share_crankboy_bin}, "
                f"share_rom={share_rom}, roms={len(self.rom_paths)}"
            )
            self._emit(f"[1/8] Probing CrankBoy on {self.port}...")

            # The port_scanner already validated the version. Re-check
            # here defensively, in case the manager skipped the gate.
            from src.core.port_scanner import test_port
            t0 = time.time()
            status, version, scene = test_port(self.port, timeout=2.0)
            self._emit(
                f"  test_port -> status={status!r} version={version!r} "
                f"scene={scene!r} ({time.time()-t0:.2f}s)"
            )
            if status != "crankboy":
                self._emit("[FAIL] CrankBoy not detected on selected port.")
                self.all_completed.emit(False)
                return
            if not version_at_least(version, MIN_CRANKBOY_VERSION):
                self._emit(
                    f"[FAIL] CrankBoy v{'.'.join(str(x) for x in MIN_CRANKBOY_VERSION)}"
                    f" or newer is required (found {version})."
                )
                self.all_completed.emit(False)
                return

            self._emit("[2/8] Opening serial connection...")
            ser = serial.Serial(self.port, 115200, timeout=2.0)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            self._emit(f"  serial open: {self.port} @ 115200")

            # Forward every raw protocol line from the device into the
            # device_log signal so the dialog can render them in grey.
            on_dev_line = self.device_log.emit

            self._emit("[3/8] Querying cb:pdxpath...")
            t0 = time.time()
            pdxpath = cb_pdxpath(ser, on_line=on_dev_line)
            self._emit(f"  pdxpath result: {pdxpath!r} ({time.time()-t0:.2f}s)")
            if not pdxpath:
                self._emit("[FAIL] Could not query CrankBoy pdx path (cb:pdxpath).")
                self.all_completed.emit(False)
                return

            self._emit("[4/8] Querying cb:pdxinfo...")
            t0 = time.time()
            pdxinfo = cb_pdxinfo(ser, on_line=on_dev_line)
            self._emit(f"  pdxinfo result: {pdxinfo!r} ({time.time()-t0:.2f}s)")
            if not pdxinfo:
                self._emit("[FAIL] Could not query CrankBoy pdxinfo (cb:pdxinfo).")
                self.all_completed.emit(False)
                return
            crankboy_bundle_id = pdxinfo.get("bundleID", "")
            self._emit(f"  CrankBoy bundleID = {crankboy_bundle_id!r}")

            if share_crankboy_bin:
                self._emit("[5/8] Installing shared crankboy.bin on device (cb:fwdinstall)...")
                t0 = time.time()
                fwd_install_path = cb_fwdinstall(ser, on_line=on_dev_line)
                self._emit(
                    f"  fwdinstall result: {fwd_install_path!r} ({time.time()-t0:.2f}s)"
                )
                if not fwd_install_path:
                    self._emit("[FAIL] cb:fwdinstall failed -- aborting.")
                    self.all_completed.emit(False)
                    return
            else:
                self._emit("[5/8] Skipping cb:fwdinstall (share_crankboy_bin=False).")

            self._emit("[6/8] Switching Playdate to data-disk mode...")
            data_disk.enter_data_disk_mode(ser, log=lambda m: self._emit("  " + m))
            try:
                ser.close()
            except Exception as e:
                self._emit(f"  serial.close raised: {e!r}")
            ser = None

            self._emit("  waiting for PLAYDATE volume to appear...")
            t0 = time.time()
            mount = data_disk.find_mount(
                timeout=25.0, log=lambda m: self._emit("  " + m)
            )
            self._emit(f"  mount discovery: {mount!r} ({time.time()-t0:.2f}s)")
            if not mount:
                self._emit(
                    "[FAIL] Could not find the PLAYDATE volume after switching "
                    "to data-disk mode. Is auto-mount enabled?"
                )
                self.all_completed.emit(False)
                return

            crankboy_bin_source = None
            if not share_crankboy_bin:
                self._emit("[6b/8] Locating crankboy.bin on data disk...")
                tried = _crankboy_bin_candidates(mount, pdxpath, crankboy_bundle_id)
                for c in tried:
                    exists = os.path.isfile(c)
                    self._emit(f"  candidate: {c} (exists={exists})")
                    if exists and crankboy_bin_source is None:
                        crankboy_bin_source = c
                if not crankboy_bin_source:
                    self._emit(
                        "[FAIL] Could not locate crankboy.bin on the data disk."
                    )
                    self._eject_quiet(mount)
                    self.all_completed.emit(False)
                    return
                size = os.path.getsize(crankboy_bin_source)
                self._emit(
                    f"  selected: {crankboy_bin_source} ({size} bytes)"
                )

            games_dir = os.path.join(mount, "Games")
            self._emit(f"[7/8] Writing forwarders into {games_dir}...")
            os.makedirs(games_dir, exist_ok=True)

            overall_success = True
            for rom_path in self.rom_paths:
                if not self._is_running:
                    self._emit("  stop() requested; aborting remaining ROMs.")
                    overall_success = False
                    break
                # For non-shared forwarders, copy fonts/ and images/ from
                # the source CrankBoy .pdx on the mount into each new
                # forwarder's .pdx, so loadFont/loadBitmap calls inside
                # crankboy.bin resolve without /Shared/.forwarder/.
                crankboy_pdx_root = None
                if not share_crankboy_bin and pdxpath:
                    candidate = os.path.join(mount, pdxpath.lstrip("/"))
                    if os.path.isdir(candidate):
                        crankboy_pdx_root = candidate

                ok, msg, install_pdx = self._build_one(
                    rom_path=rom_path,
                    mount=mount,
                    out_parent_dir=games_dir,
                    share_crankboy_bin=share_crankboy_bin,
                    share_rom=share_rom,
                    pdxinfo=pdxinfo,
                    fwd_install_path=fwd_install_path,
                    crankboy_bin_source=crankboy_bin_source,
                    crankboy_pdx_root=crankboy_pdx_root,
                    db_title=db_titles.get(rom_path),
                    launcher_icon=(self.options.get("launcher_icons", {}) or {}).get(rom_path),
                    launcher_card=(self.options.get("launcher_cards", {}) or {}).get(rom_path),
                    download_art=download_art,
                )
                self.rom_completed.emit(rom_path, ok, msg)
                if ok and install_pdx:
                    self.forwarder_installed.emit(rom_path, install_pdx)
                if not ok:
                    overall_success = False

            self._emit("[8/8] Ejecting Playdate...")
            t0 = time.time()
            ejected = self._eject_quiet(mount)
            self._emit(
                f"  eject -> {ejected} ({time.time()-t0:.2f}s)"
            )
            if not ejected:
                self._emit(
                    "  Eject failed -- please eject manually before reconnecting."
                )

            self._emit(
                "=== Forwarder build %s ===" % (
                    "completed" if overall_success else "completed WITH ERRORS"
                )
            )
            self.all_completed.emit(overall_success)
        except Exception as e:
            self._emit(f"[CRASH] Forwarder worker exception: {e!r}")
            import traceback
            for line in traceback.format_exc().splitlines():
                self._emit("  " + line)
            self.all_completed.emit(False)
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass

    def _build_one(self, *, rom_path, mount, out_parent_dir, share_crankboy_bin,
                   share_rom, pdxinfo, fwd_install_path, crankboy_bin_source,
                   db_title, launcher_icon=None, launcher_card=None,
                   crankboy_pdx_root=None, download_art=True):
        base = os.path.basename(rom_path)
        try:
            self._emit(f"  > {base}: assembling .pdx (db_title={db_title!r})")
            with tempfile.TemporaryDirectory(prefix="cb_fwd_") as tmp:
                pdx_dir, bundle_id, crc_hex, sanitized = build_forwarder_pdx(
                    rom_path=rom_path,
                    out_parent_dir=tmp,
                    share_crankboy_bin=share_crankboy_bin,
                    share_rom=share_rom,
                    crankboy_pdxinfo=pdxinfo,
                    fwd_install_path=fwd_install_path,
                    crankboy_bin_source=crankboy_bin_source,
                    db_title=db_title,
                    launcher_icon=launcher_icon,
                    launcher_card=launcher_card,
                    download_art=download_art,
                    log=self._emit,
                )
                self._emit(
                    f"    staged at {pdx_dir} "
                    f"(bundle_id={bundle_id}, crc={crc_hex}, name={sanitized})"
                )

                # Non-shared forwarders need fonts/ and images/ alongside
                # crankboy.bin so the engine asset loads resolve out of
                # the local .pdx/. Pull them from the source CrankBoy .pdx
                # on the mounted data disk.
                if crankboy_pdx_root:
                    self._copy_engine_assets(crankboy_pdx_root, pdx_dir)

                dest = os.path.join(out_parent_dir, os.path.basename(pdx_dir))
                if os.path.exists(dest):
                    self._emit(f"    overwriting existing {dest}")
                    shutil.rmtree(dest)
                self._emit(f"    copying -> {dest}")
                shutil.copytree(pdx_dir, dest)

            # If share_rom, also push the ROM into the shared games dir so
            # the forwarder's bundle.json["rom"] absolute path resolves.
            if share_rom:
                self._push_shared_rom(mount, rom_path)

            # Device-side path (relative to the data-disk root with leading /).
            install_pdx = "/Games/" + os.path.basename(dest)
            return True, f"Installed {os.path.basename(dest)}", install_pdx
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._emit(f"  > {base}: build failed: {e!r}")
            for line in tb.splitlines():
                self._emit("    " + line)
            return False, f"Failed: {e!r}", None

    # Asset dirs the device-side CB_install_shared_forwarder copies into
    # /Shared/.forwarder/<id>/. Mirrored here for the non-shared path so
    # forwarders without a shared crankboy.bin still have their engine
    # assets locally.
    _ENGINE_ASSET_DIRS = ("fonts", "images")

    def _copy_engine_assets(self, crankboy_pdx_root, dest_pdx_dir):
        """Copy fonts/ and images/ from a mounted CrankBoy .pdx into the
        forwarder's staged .pdx directory.
        """
        for name in self._ENGINE_ASSET_DIRS:
            src = os.path.join(crankboy_pdx_root, name)
            dst = os.path.join(dest_pdx_dir, name)
            if not os.path.isdir(src):
                self._emit(f"    asset dir missing on device: {src}")
                continue
            try:
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                self._emit(f"    copied {name}/ from device .pdx")
            except Exception as e:
                self._emit(f"    failed to copy {name}/ from {src}: {e!r}")

    def _push_shared_rom(self, mount, rom_path):
        """Copy the ROM into /Shared/Emulation/gb/games/<basename> on the
        mounted data disk, unless an identical-size copy is already there.
        """
        from src.core.forwarder_builder import DEFAULT_SHARED_BASE_DIR
        basename = os.path.basename(rom_path)
        rel = DEFAULT_SHARED_BASE_DIR.lstrip("/")  # "Shared/Emulation/gb"
        target_dir = os.path.join(mount, rel, "games")
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, basename)
        src_size = os.path.getsize(rom_path)
        if os.path.isfile(target) and os.path.getsize(target) == src_size:
            self._emit(
                f"    shared ROM already at {target} ({src_size} B), skipping copy"
            )
            return
        self._emit(f"    pushing ROM -> {target} ({src_size} B)")
        shutil.copy(rom_path, target)

    def _eject_quiet(self, mount):
        try:
            return data_disk.eject(mount, log=lambda m: self._emit("  " + m))
        except Exception as e:
            self._emit(f"  eject raised: {e!r}")
            return False
