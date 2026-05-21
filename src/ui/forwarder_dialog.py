"""Standalone window for building a CrankBoy launcher (forwarder) .pdx.

The window can be opened with or without a connected CrankBoy. Its own
copy of the main connection-status banner reflects live device state via
the parent main window's `connection_changed` signal. The action button
("Add to Device") is greyed out unless CrankBoy is connected and meets
the minimum version.
"""

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedLayout,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.forwarder_worker import ForwarderWorker, LauncherCardWorker, MIN_CRANKBOY_VERSION
from src.core.database import database as rom_database
from src.core.transfer_engine import calculate_crc32, version_at_least
from src.ui.spinner import Spinner


VALID_ROM_EXTS = ('.gb', '.gbc', '.gbz')
VALID_IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif')

# Pixel size of the in-dialog icon preview. The real launcher icon is
# 32x32; we render it at 4x for visibility.
_ICON_PREVIEW_PX = 32 * 4

# Real Playdate launcher card is 350x155; show 1:1 in the dialog.
_CARD_PREVIEW_W = 350
_CARD_PREVIEW_H = 155


class _CardDropTarget(QLabel):
    """Drop target for the launcher card preview (350x155). Same drop
    semantics as `_IconDropTarget`.
    """

    def __init__(self, on_dropped, parent=None):
        super().__init__(parent)
        self._on_dropped = on_dropped
        self.setFixedSize(_CARD_PREVIEW_W, _CARD_PREVIEW_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setStyleSheet(
            "QLabel { border: 1px dashed palette(mid); "
            "border-radius: 4px; background: palette(base); }"
        )
        self.setToolTip(
            "Drop a PNG/JPG/BMP/GIF to override the auto-generated card"
        )

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and path.lower().endswith(VALID_IMAGE_EXTS):
                self._on_dropped(path)
                event.acceptProposedAction()
                return
        event.ignore()


class _IconDropTarget(QLabel):
    """Small preview that accepts image drops to override the launcher
    icon. Calls `on_dropped(path)` whenever a valid image is dropped on it.
    """

    def __init__(self, on_dropped, parent=None):
        super().__init__(parent)
        self._on_dropped = on_dropped
        self.setFixedSize(_ICON_PREVIEW_PX, _ICON_PREVIEW_PX)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setStyleSheet(
            "QLabel { border: 1px dashed palette(mid); "
            "border-radius: 4px; background: palette(base); }"
        )
        self.setToolTip(
            "Drop a PNG/JPG/BMP/GIF to override the auto-generated icon"
        )

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and path.lower().endswith(VALID_IMAGE_EXTS):
                self._on_dropped(path)
                event.acceptProposedAction()
                return
        event.ignore()


class _RomDropTarget(QLabel):
    """Big drop area for one ROM file."""
    rom_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(80)
        self.setWordWrap(True)
        self.setAcceptDrops(True)
        self.set_placeholder()

    def set_placeholder(self):
        self.setText("Drag a .gb / .gbc / .gbz file here, or click Browse…")
        self.setStyleSheet(
            "QLabel { border: 2px dashed palette(mid); "
            "border-radius: 6px; padding: 16px; color: palette(mid); }"
        )

    def set_rom(self, path):
        self.setText(path)
        self.setStyleSheet(
            "QLabel { border: 2px solid palette(highlight); "
            "border-radius: 6px; padding: 16px; }"
        )

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if path and path.lower().endswith(VALID_ROM_EXTS):
                self.rom_dropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()


class ForwarderDialog(QDialog):
    """Standalone forwarder builder window."""

    worker_started = pyqtSignal()
    worker_finished = pyqtSignal()

    def __init__(self, parent, log_callback=None):
        super().__init__(parent)
        self._parent_window = parent  # MainWindow (or None in tests)
        self._log_callback = log_callback
        self._worker = None
        self._rom_path = None
        self._connection_kind = None
        # Tracks the device version that informs the Add-to-Device
        # gate. The scene gate lives in the main window's
        # _is_blocking_scene -> _NON_BLOCKING_SCENES allowlist; if we
        # show "Connected" here it's already library-or-sft-modal.
        self._device_version = None

        self.setWindowTitle("Create CrankBoy Launcher Forwarder")
        # Min height has to comfortably fit: banner (~30) + ROM box
        # (~155) + config box (icon row 128 + card row 155 +
        # checkboxes 60 + padding ~40) + action row (~40) + log view
        # min (100) + outer margins (~30).
        self.setMinimumSize(600, 780)
        self.setModal(False)
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # --- Connection banner (mirrors the main window's banner) ---
        self.banner = QLabel()
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setVisible(False)
        layout.addWidget(self.banner)

        # --- ROM selection ---
        rom_box = QGroupBox("ROM")
        rom_layout = QVBoxLayout(rom_box)
        self.drop_target = _RomDropTarget()
        self.drop_target.rom_dropped.connect(self._set_rom)
        rom_layout.addWidget(self.drop_target)

        rom_buttons = QHBoxLayout()
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._on_browse_clicked)
        rom_buttons.addWidget(self.browse_btn)
        self.clear_rom_btn = QPushButton("Clear")
        self.clear_rom_btn.clicked.connect(self._clear_rom)
        self.clear_rom_btn.setEnabled(False)
        rom_buttons.addWidget(self.clear_rom_btn)
        rom_buttons.addStretch()
        rom_layout.addLayout(rom_buttons)
        layout.addWidget(rom_box)

        # --- Configuration ---
        config_box = QGroupBox("Configuration")
        config_layout = QVBoxLayout(config_box)

        self.share_crankboy_bin_cb = QCheckBox("Share crankboy.bin")
        self.share_crankboy_bin_cb.setChecked(True)
        self.share_crankboy_bin_cb.setToolTip(
            "All forwarders share a common crankboy binary "
            "(saves a few megabytes with many forwarders)"
        )
        config_layout.addWidget(self.share_crankboy_bin_cb)

        self.share_rom_cb = QCheckBox("Share ROM, saves, save states, etc.")
        self.share_rom_cb.setChecked(True)
        self.share_rom_cb.setToolTip(
            "Share the game ROM, save files, save states, etc. with "
            "the main CrankBoy app"
        )
        config_layout.addWidget(self.share_rom_cb)

        # Launcher list-icon preview row.
        icon_row = QHBoxLayout()
        self.icon_preview = _IconDropTarget(self._on_icon_dropped)
        icon_row.addWidget(self.icon_preview)

        icon_buttons = QVBoxLayout()
        icon_buttons.addWidget(QLabel("Launcher icon"))
        self.icon_browse_btn = QPushButton("Browse…")
        self.icon_browse_btn.clicked.connect(self._on_icon_browse_clicked)
        icon_buttons.addWidget(self.icon_browse_btn)
        self.icon_clear_btn = QPushButton("Clear")
        self.icon_clear_btn.setToolTip(
            "Revert to the auto-generated icon for this ROM"
        )
        self.icon_clear_btn.clicked.connect(self._on_icon_clear_clicked)
        icon_buttons.addWidget(self.icon_clear_btn)
        icon_buttons.addStretch()
        icon_row.addLayout(icon_buttons)
        icon_row.addStretch()
        config_layout.addLayout(icon_row)

        # Custom icon supplied via Browse/drop, or None to use the
        # auto-generated one. The auto icon is recomputed whenever the
        # ROM (and thus title) changes.
        self._user_icon = None      # PIL.Image (RGBA, 32x32) or None
        # _auto_icon is set by LauncherCardWorker when the crankboy-bundles
        # manifest has a hand-curated override; it takes precedence over
        # the locally-computed `_compose_auto_icon()` result.
        self._auto_icon = None
        self._effective_icon = None  # last rendered preview
        self._refresh_icon_preview()

        # --- Launcher card preview row. ---
        # The card composer hits the network (libretro -> CrankBoy DB ->
        # text fallback), so it runs in a LauncherCardWorker and the UI
        # shows a spinner until the cache is ready.
        card_row = QHBoxLayout()
        # Container that holds the card preview QLabel and a centered
        # Spinner; we toggle which is visible via QStackedLayout so the
        # 350x155 footprint stays stable as the build state changes.
        self.card_container = QWidget()
        self.card_container.setFixedSize(_CARD_PREVIEW_W, _CARD_PREVIEW_H)
        card_stack = QStackedLayout(self.card_container)
        card_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        card_stack.setContentsMargins(0, 0, 0, 0)

        self.card_preview = _CardDropTarget(self._on_card_dropped)
        card_stack.addWidget(self.card_preview)

        # Wrap the Spinner in a transparent container so we can center
        # it over the card preview without resizing the spinner itself.
        spinner_holder = QWidget()
        spinner_layout = QHBoxLayout(spinner_holder)
        spinner_layout.setContentsMargins(0, 0, 0, 0)
        spinner_layout.addStretch()
        self.card_spinner = Spinner(size=32)
        spinner_layout.addWidget(self.card_spinner)
        spinner_layout.addStretch()
        card_stack.addWidget(spinner_holder)
        self._card_spinner_holder = spinner_holder

        card_row.addWidget(self.card_container)

        card_buttons = QVBoxLayout()
        card_buttons.addWidget(QLabel("Launcher card"))
        # Top-to-bottom: Browse / Preview / Clear. Preview lives in this
        # column rather than the bottom action row because it's
        # conceptually a card-art operation, not a forwarder-install one.
        self.card_browse_btn = QPushButton("Browse…")
        self.card_browse_btn.clicked.connect(self._on_card_browse_clicked)
        card_buttons.addWidget(self.card_browse_btn)
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.setEnabled(False)
        self.preview_btn.setToolTip(
            "Show the launcher card on the connected Playdate's screen"
        )
        self.preview_btn.clicked.connect(self._on_preview_clicked)
        card_buttons.addWidget(self.preview_btn)
        self.card_clear_btn = QPushButton("Clear")
        self.card_clear_btn.setToolTip(
            "Revert to the auto-generated card for this ROM"
        )
        self.card_clear_btn.clicked.connect(self._on_card_clear_clicked)
        card_buttons.addWidget(self.card_clear_btn)
        card_buttons.addStretch()
        card_row.addLayout(card_buttons)
        card_row.addStretch()
        config_layout.addLayout(card_row)

        # Card state (mirrors the icon state):
        #   _user_card    -- user-supplied override, drops to None on Clear.
        #   _auto_card    -- last successful auto-build, persistent cache;
        #                    Clear restores from here.
        #   _card_worker  -- in-flight LauncherCardWorker (cleared on done).
        #   _card_worker_rom -- rom_path the worker was started for, so
        #                    a stale result for a previously-selected ROM
        #                    can be ignored.
        self._user_card = None
        self._auto_card = None
        self._card_worker = None
        self._card_worker_rom = None
        # Initial paint (no ROM selected yet -> empty placeholder).
        self._refresh_card_preview()

        layout.addWidget(config_box)

        # --- Actions ---
        action_layout = QHBoxLayout()
        self.create_btn = QPushButton("Add to Device")
        self.create_btn.setEnabled(False)
        self.create_btn.clicked.connect(self._on_create_clicked)
        action_layout.addWidget(self.create_btn)

        self.launch_btn = QPushButton("Launch Now")
        self.launch_btn.setEnabled(False)
        self.launch_btn.setToolTip("Install forwarder first")
        self.launch_btn.clicked.connect(self._on_launch_clicked)
        action_layout.addWidget(self.launch_btn)

        action_layout.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        action_layout.addWidget(self.close_btn)
        layout.addLayout(action_layout)

        # device-side path of the most recent successful install
        # (e.g. /Games/CrankBoy_fwd_<crc>_<name>.pdx). Drives Launch Now.
        self._last_installed_pdx = None

        # --- Log ---
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Progress will appear here.")
        layout.addWidget(self.log_view, stretch=1)

        # Hook into the main window's connection-state stream.
        if parent is not None and hasattr(parent, "connection_changed"):
            try:
                parent.connection_changed.connect(self._on_connection_changed)
            except Exception:
                pass
            # Snapshot whatever state is current right now.
            try:
                self._on_connection_changed(parent.current_connection_state())
            except Exception:
                self._on_connection_changed(None)
        # Even with no parent we still want the gate to be evaluated.
        self._update_action_button()

    # --- ROM selection helpers ---

    def _on_browse_clicked(self):
        filter_str = "Game Boy ROMs (*.gb *.gbc *.gbz);;All files (*)"
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a ROM", "", filter_str
        )
        if path:
            self._set_rom(path)

    def _set_rom(self, path):
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Invalid ROM", f"File not found: {path}")
            return
        if not path.lower().endswith(VALID_ROM_EXTS):
            QMessageBox.warning(
                self, "Invalid ROM",
                "Pick a .gb, .gbc, or .gbz file."
            )
            return
        self._rom_path = path
        self.drop_target.set_rom(path)
        self.clear_rom_btn.setEnabled(True)
        # New ROM -> invalidate cached auto card AND auto icon (the latter
        # only matters when a bundle override was applied for the
        # previous ROM). The async card worker will repopulate both if a
        # bundle is found.
        self._auto_card = None
        self._auto_icon = None
        self._refresh_icon_preview()
        self._start_card_compose(path)
        self._refresh_card_preview()
        self._update_action_button()

    def _clear_rom(self):
        self._rom_path = None
        self.drop_target.set_placeholder()
        self.clear_rom_btn.setEnabled(False)
        # Drop any cached card+icon and stop the worker; previews will
        # show their empty placeholders.
        self._user_card = None
        self._auto_card = None
        self._auto_icon = None
        self._refresh_icon_preview()
        self._cancel_card_worker()
        self._refresh_card_preview()
        self._update_action_button()

    # --- launcher icon ----------------------------------------------------

    def _auto_title_for_rom(self):
        """Compute the same name string `build_forwarder_pdx` writes into
        pdxinfo, so the auto icon mirrors the actual install.
        """
        from src.core.forwarder_builder import (
            crankboy_display_title, read_rom_header_name,
        )
        if not self._rom_path:
            return None
        try:
            with open(self._rom_path, "rb") as f:
                data = f.read()
            crc = calculate_crc32(data)
            info = rom_database.lookup(crc)
            fallback = os.path.splitext(os.path.basename(self._rom_path))[0]
            if info:
                return crankboy_display_title(info, fallback)
            return crankboy_display_title(None, fallback)
        except Exception:
            return read_rom_header_name(self._rom_path) or None

    def _compose_auto_icon(self):
        from src.core.icon_builder import compose_launcher_icon_for_title
        title = self._auto_title_for_rom()
        if not title:
            # No ROM selected -- still show the template so the user
            # sees what the auto icon looks like.
            return compose_launcher_icon_for_title("")
        return compose_launcher_icon_for_title(title)

    def _effective_icon_image(self):
        """Return the PIL.Image actually used for this install (user
        override if set, otherwise the auto-composed icon).
        """
        if self._user_icon is not None:
            return self._user_icon
        if self._auto_icon is not None:
            return self._auto_icon
        return self._compose_auto_icon()

    def _refresh_icon_preview(self):
        from PIL import Image
        img = self._effective_icon_image()
        self._effective_icon = img
        # Scale up 4x to make the 32x32 visible, nearest-neighbour to
        # keep pixels sharp.
        big = img.resize(
            (_ICON_PREVIEW_PX, _ICON_PREVIEW_PX), Image.NEAREST
        )
        # PIL -> QPixmap.
        from PyQt6.QtGui import QImage, QPixmap
        rgba = big.tobytes("raw", "RGBA")
        qimg = QImage(rgba, big.width, big.height, big.width * 4,
                      QImage.Format.Format_RGBA8888)
        # QPixmap.fromImage needs the QImage data to outlive it, so
        # detach by copying.
        self.icon_preview.setPixmap(QPixmap.fromImage(qimg).copy())
        self.icon_clear_btn.setEnabled(self._user_icon is not None)

    def _on_icon_browse_clicked(self):
        filt = "Images (*.png *.jpg *.jpeg *.bmp *.gif);;All files (*)"
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick a launcher icon", "", filt
        )
        if path:
            self._on_icon_dropped(path)

    def _on_icon_dropped(self, path):
        from PIL import Image
        try:
            img = Image.open(path)
        except Exception as e:
            QMessageBox.warning(
                self, "Invalid image", f"Could not open {path}: {e!r}"
            )
            return
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.size != (32, 32):
            img = img.resize((32, 32), Image.LANCZOS)
        self._user_icon = img
        self._refresh_icon_preview()

    def _on_icon_clear_clicked(self):
        self._user_icon = None
        self._refresh_icon_preview()

    # --- launcher card ----------------------------------------------------

    def _start_card_compose(self, rom_path):
        """Kick off the LauncherCardWorker for `rom_path`. Cancels any
        in-flight worker first.
        """
        self._cancel_card_worker()
        # Compute DB-derived title and rom_info the same way the build
        # pipeline does (so the preview matches what gets installed).
        try:
            with open(rom_path, "rb") as f:
                data = f.read()
            crc = calculate_crc32(data)
            rom_info = rom_database.lookup(crc)
        except Exception:
            rom_info = None
        from src.core.forwarder_builder import crankboy_display_title
        filename_fallback = os.path.splitext(os.path.basename(rom_path))[0]
        title = crankboy_display_title(rom_info, filename_fallback)

        worker = LauncherCardWorker(rom_path, title, rom_info, parent=self)
        worker.card_ready.connect(self._on_card_ready)
        self._card_worker = worker
        self._card_worker_rom = rom_path
        worker.start()

    def _cancel_card_worker(self):
        """Detach (but don't kill) the current worker. We intentionally
        let it finish so the cache populates even if the user replaces
        the art mid-build -- the rom_path check in _on_card_ready
        ignores stale results for a previously-selected ROM.
        """
        # Just clear our tracking; the QThread keeps running and emits
        # card_ready when done. We still need the connection so the
        # cache for the original rom_path gets populated.
        self._card_worker = None
        # _card_worker_rom intentionally NOT cleared: leave the stale
        # worker's emission free to populate _auto_card if the user is
        # still on the same rom_path. If they've moved to a different
        # ROM, _on_card_ready will see rom_path != self._rom_path and
        # drop the stale result.

    def _on_card_ready(self, rom_path, card_img, icon_img):
        """Background worker finished -- if the result corresponds to
        the ROM that's still selected, cache it; otherwise discard.

        `icon_img` is non-None only when the crankboy-bundles manifest
        provided a hand-curated override; in that case we also flip
        the cached auto-icon so the dialog (and subsequent build) use
        the bundle's icon instead of the locally-computed one.
        """
        if rom_path != self._rom_path:
            return
        self._auto_card = card_img
        if icon_img is not None:
            self._auto_icon = icon_img
            # Re-paint the icon preview so the bundle's icon takes over
            # immediately (unless the user has already supplied their
            # own override via Browse/drop).
            self._refresh_icon_preview()
        self._refresh_card_preview()

    def _refresh_card_preview(self):
        """Repaint the card preview based on current state."""
        if not self._rom_path:
            # No ROM yet -> show empty placeholder, no spinner.
            self.card_preview.setPixmap(self._empty_card_pixmap())
            self._set_card_spinner_visible(False)
            self.card_clear_btn.setEnabled(False)
            return

        effective = self._user_card if self._user_card is not None else self._auto_card
        if effective is None:
            # Auto compose still pending -> spinner over empty preview.
            self.card_preview.setPixmap(self._empty_card_pixmap())
            self._set_card_spinner_visible(True)
        else:
            self._set_card_spinner_visible(False)
            self.card_preview.setPixmap(self._pil_to_card_pixmap(effective))
        self.card_clear_btn.setEnabled(self._user_card is not None)

    def _set_card_spinner_visible(self, visible):
        # The spinner widget hides itself when not visible (it pauses
        # its animation timer in hideEvent).
        if visible:
            self._card_spinner_holder.show()
            self.card_spinner.show()
        else:
            self.card_spinner.hide()
            self._card_spinner_holder.hide()

    def _empty_card_pixmap(self):
        """Placeholder shown before any auto-build finishes (and while
        loading): the unmodified bundled LauncherCard.png. Cached
        across calls.
        """
        if getattr(self, "_blank_card_pixmap", None) is None:
            from src.core.icon_builder import _assets_dir
            try:
                from PIL import Image
                img = Image.open(_assets_dir() / "LauncherCard.png")
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                if img.size != (_CARD_PREVIEW_W, _CARD_PREVIEW_H):
                    img = img.resize(
                        (_CARD_PREVIEW_W, _CARD_PREVIEW_H), Image.NEAREST
                    )
                self._blank_card_pixmap = self._pil_to_card_pixmap(img)
            except Exception:
                # Asset missing -> fall back to a transparent surface so
                # the spinner still renders cleanly on top.
                from PyQt6.QtGui import QPixmap
                from PyQt6.QtCore import Qt as _Qt
                pm = QPixmap(_CARD_PREVIEW_W, _CARD_PREVIEW_H)
                pm.fill(_Qt.GlobalColor.transparent)
                self._blank_card_pixmap = pm
        return self._blank_card_pixmap

    def _pil_to_card_pixmap(self, img):
        from PyQt6.QtGui import QImage, QPixmap
        from PIL import Image
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.size != (_CARD_PREVIEW_W, _CARD_PREVIEW_H):
            img = img.resize((_CARD_PREVIEW_W, _CARD_PREVIEW_H), Image.NEAREST)
        rgba = img.tobytes("raw", "RGBA")
        qimg = QImage(rgba, img.width, img.height, img.width * 4,
                      QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimg).copy()

    def _on_card_browse_clicked(self):
        filt = "Images (*.png *.jpg *.jpeg *.bmp *.gif);;All files (*)"
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick a launcher card", "", filt
        )
        if path:
            self._on_card_dropped(path)

    def _on_card_dropped(self, path):
        from PIL import Image
        try:
            img = Image.open(path)
        except Exception as e:
            QMessageBox.warning(
                self, "Invalid image", f"Could not open {path}: {e!r}"
            )
            return
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.size != (_CARD_PREVIEW_W, _CARD_PREVIEW_H):
            img = img.resize((_CARD_PREVIEW_W, _CARD_PREVIEW_H), Image.LANCZOS)
        self._user_card = img
        # NB: we deliberately do NOT cancel the async auto-build. Per
        # the spec, the cache still gets populated so Clear restores
        # the auto card later.
        self._refresh_card_preview()

    def _on_card_clear_clicked(self):
        self._user_card = None
        self._refresh_card_preview()

    # --- Drag-and-drop directly on the dialog as a whole ---

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if path and path.lower().endswith(VALID_ROM_EXTS):
                self._set_rom(path)
                event.acceptProposedAction()
                return
        event.ignore()

    # --- Connection state ---

    def _on_connection_changed(self, state):
        """Handle banner/state updates from the main window.

        `state` is (kind, text, bg, fg) or None when the banner should be
        hidden.
        """
        if state is None:
            self._connection_kind = None
            self.banner.setVisible(False)
        else:
            kind, text, bg, fg = state
            self._connection_kind = kind
            self.banner.setText(text)
            self.banner.setStyleSheet(
                f"QLabel {{ background-color: {bg}; color: {fg};"
                f" font-weight: bold; padding: 8px; }}"
            )
            self.banner.setVisible(True)

        # Snapshot the device version (used by the version gate). We
        # query the main window each time, because connection_changed
        # only fires on banner-kind changes.
        if self._parent_window is not None and hasattr(self._parent_window, "current_port_info"):
            try:
                info = self._parent_window.current_port_info() or {}
                self._device_version = info.get('version')
            except Exception:
                self._device_version = None
        else:
            self._device_version = None

        self._update_action_button()
        self._update_launch_button()
        self._update_preview_button()

    def _connection_ready(self):
        """True when the device is in a manager-compatible scene (the
        main window's banner says "Connected") and the device version
        is recent enough for forwarders. Scene checking lives in the
        main window's _NON_BLOCKING_SCENES allowlist so the main
        banner and the dialog agree.
        """
        if self._connection_kind != 'connected':
            return False
        if not self._device_version:
            return False
        if not version_at_least(self._device_version, MIN_CRANKBOY_VERSION):
            return False
        return True

    def _update_action_button(self):
        busy = self._worker is not None
        ready = self._connection_ready()
        has_rom = self._rom_path is not None
        self.create_btn.setEnabled(not busy and ready and has_rom)

        # Inform the user what's missing.
        if busy:
            self.create_btn.setToolTip("Build in progress")
        elif not has_rom:
            self.create_btn.setToolTip("Pick a ROM first")
        elif self._connection_kind != 'connected':
            self.create_btn.setToolTip(
                "Waiting for CrankBoy on the library screen."
            )
        elif not self._device_version or not version_at_least(
            self._device_version, MIN_CRANKBOY_VERSION
        ):
            min_str = ".".join(str(x) for x in MIN_CRANKBOY_VERSION)
            self.create_btn.setToolTip(
                f"CrankBoy {min_str}+ is required (device reports "
                f"{self._device_version or 'unknown'})."
            )
        else:
            self.create_btn.setToolTip("")

    # --- Build ---

    def _on_create_clicked(self):
        if self._worker is not None:
            return
        if not self._rom_path:
            return
        if not self._connection_ready():
            return

        # Look up the active port from the parent window at click time.
        port = None
        if self._parent_window is not None and hasattr(self._parent_window, "current_port_info"):
            try:
                info = self._parent_window.current_port_info() or {}
                # The port info dict doesn't itself include the device
                # path -- use the parent's combo for that.
                port_combo = getattr(self._parent_window, "port_combo", None)
                port = port_combo.currentData() if port_combo else None
            except Exception:
                port = None
        if not port:
            QMessageBox.warning(
                self, "No device",
                "Could not resolve the selected serial port. "
                "Reconnect your Playdate and try again."
            )
            return

        # Look up a friendly title from the DB (best-effort). Match the
        # name CrankBoy itself displays: prefer DB's "short" (already
        # parenthetical/bracket-stripped), then apply common_article_form
        # so trailing ", The" / ", La" / ", Der" etc. move to the front.
        # If the ROM isn't in the DB, fall back to the filename without
        # its extension, with the same stripping/article handling applied.
        db_title = None
        filename_fallback = os.path.splitext(os.path.basename(self._rom_path))[0]
        try:
            self._log(f"Computing CRC32 of {os.path.basename(self._rom_path)}...")
            with open(self._rom_path, "rb") as f:
                data = f.read()
            crc = calculate_crc32(data)
            self._log(f"  CRC32 = {crc:08X}")
            rom_info = rom_database.lookup(crc)
            from src.core.forwarder_builder import crankboy_display_title
            if rom_info:
                db_title = crankboy_display_title(rom_info, filename_fallback)
                self._log(f"  DB title: {db_title}")
            else:
                db_title = crankboy_display_title(None, filename_fallback)
                self._log(f"  (no DB entry; using filename) -> {db_title}")
        except Exception as e:
            self._log(f"  CRC/DB lookup failed: {e!r}")

        # Snapshot the icon being shown in the preview right now -- that
        # is the user override if there is one, else the auto-composed
        # one. The worker passes it verbatim to build_forwarder_pdx.
        icon_for_install = self._effective_icon_image()
        # Same for the launcher card: prefer the user override, else
        # the cached auto-build. If the async build hasn't finished yet
        # (rare -- user hit Add before the spinner cleared), pass None
        # and let build_forwarder_pdx re-compose synchronously in the
        # build worker thread.
        card_for_install = (
            self._user_card if self._user_card is not None else self._auto_card
        )
        options = {
            'share_crankboy_bin': self.share_crankboy_bin_cb.isChecked(),
            'share_rom': self.share_rom_cb.isChecked(),
            'db_titles': {self._rom_path: db_title} if db_title else {},
            'launcher_icons':
                {self._rom_path: icon_for_install} if icon_for_install else {},
            'launcher_cards':
                {self._rom_path: card_for_install} if card_for_install else {},
        }
        self._log(
            "Options: share_crankboy_bin=%s, share_rom=%s" % (
                options['share_crankboy_bin'], options['share_rom']
            )
        )

        # Lock the UI while the worker runs.
        self._set_busy(True)
        self._log("Starting forwarder build on %s..." % port)

        # New install -> clear any prior Launch-Now target until we hear
        # back about a fresh successful install.
        self._last_installed_pdx = None
        self._update_launch_button()
        self._update_preview_button()

        self._worker = ForwarderWorker(port, [self._rom_path], options)
        # ForwarderWorker._emit fires both progress and log_message with the
        # same payload, so only connect one to avoid duplicate lines.
        self._worker.log_message.connect(self._log)
        self._worker.device_log.connect(self._log_device)
        self._worker.rom_completed.connect(self._on_rom_completed)
        self._worker.forwarder_installed.connect(self._on_forwarder_installed)
        self._worker.all_completed.connect(self._on_all_completed)
        self.worker_started.emit()
        self._worker.start()

    def _on_rom_completed(self, rom_path, success, message):
        self._log(f"{os.path.basename(rom_path)}: {message}")

    def _on_forwarder_installed(self, rom_path, install_pdx):
        """Record the device-side path of the freshly installed forwarder."""
        self._last_installed_pdx = install_pdx
        self._update_launch_button()
        self._update_preview_button()

    def _on_all_completed(self, overall_success):
        self._log(
            "Forwarder build complete."
            if overall_success
            else "Failed to build forwarder"
        )
        if not overall_success:
            self._flash_error_banner("Forwarder installation failed", duration_ms=2000)
        worker = self._worker
        self._worker = None
        if worker is not None:
            # Wait for the QThread to truly finish before letting Qt
            # destroy it, otherwise we get
            #   "QThread: Destroyed while thread '' is still running".
            try:
                worker.wait(3000)
            except Exception:
                pass
            worker.deleteLater()
        self._set_busy(False)
        self.worker_finished.emit()

    def _set_busy(self, busy):
        self.browse_btn.setEnabled(not busy)
        self.clear_rom_btn.setEnabled(not busy and self._rom_path is not None)
        self.share_crankboy_bin_cb.setEnabled(not busy)
        self.share_rom_cb.setEnabled(not busy)
        self._update_action_button()
        self._update_launch_button()
        self._update_preview_button()

    def _update_launch_button(self):
        """Enable Launch Now once we have a last successful install and an
        accessible Playdate on USB. The firmware's `run` command works even
        when CrankBoy isn't running, so only the device's reachability
        matters here, not whether CrankBoy is foregrounded.
        """
        busy = self._worker is not None
        has_install = bool(self._last_installed_pdx)
        device_reachable = self._device_reachable()
        self.launch_btn.setEnabled(not busy and has_install and device_reachable)
        if busy:
            self.launch_btn.setToolTip("Build in progress")
        elif not has_install:
            self.launch_btn.setToolTip("Install forwarder first")
        elif not device_reachable:
            self.launch_btn.setToolTip("Reconnect your Playdate to launch")
        else:
            self.launch_btn.setToolTip(
                f"Launch {self._last_installed_pdx} on the device"
            )

    def _device_reachable(self):
        """True iff the Playdate is on USB and accessible (regardless of
        whether CrankBoy is foregrounded).
        """
        if self._parent_window is None:
            return False
        if not hasattr(self._parent_window, "current_port_info"):
            return False
        try:
            info = self._parent_window.current_port_info() or {}
        except Exception:
            return False
        return bool(info) and info.get('accessible', False)

    def _on_launch_clicked(self):
        """Send `run <pdx>` over serial to boot the forwarder on the device."""
        if not self._last_installed_pdx:
            return
        port_combo = getattr(self._parent_window, "port_combo", None)
        port = port_combo.currentData() if port_combo else None
        if not port:
            QMessageBox.warning(
                self, "No device",
                "Could not resolve the serial port. Reconnect your Playdate."
            )
            return
        try:
            import serial as _serial
            ser = _serial.Serial(port, 115200, timeout=2.0)
            try:
                from src.core.transfer_engine import launch_pdx_path
                launch_pdx_path(ser, self._last_installed_pdx)
                self._log(f"Launched {self._last_installed_pdx}.")
            finally:
                try:
                    ser.close()
                except Exception:
                    pass
        except Exception as e:
            self._log(f"Launch failed: {e!r}")
            QMessageBox.warning(
                self, "Launch failed",
                f"Could not send launch command: {e!r}"
            )

    # --- Preview ---

    def _update_preview_button(self):
        """Enable the Preview button whenever the device is on USB,
        regardless of CrankBoy state (Preview uses the firmware's
        `bitmap` command).
        """
        busy = self._worker is not None
        reachable = self._device_reachable()
        self.preview_btn.setEnabled(not busy and reachable)
        if busy:
            self.preview_btn.setToolTip("Build in progress")
        elif not reachable:
            self.preview_btn.setToolTip("Connect your Playdate to preview")
        else:
            self.preview_btn.setToolTip(
                "Show the launcher card on the connected Playdate's screen"
            )

    def _effective_card_image(self):
        """The launcher card currently displayed in the dialog: user
        override, then cached auto, then the bare LauncherCard.png.
        Used by Preview to mirror what the user sees.
        """
        if self._user_card is not None:
            return self._user_card
        if self._auto_card is not None:
            return self._auto_card
        from src.core.icon_builder import _assets_dir, _load_pil
        try:
            return _load_pil(_assets_dir() / "LauncherCard.png")
        except Exception:
            return None

    def _on_preview_clicked(self):
        """Build the framebuffer and ship it to the device via `bitmap`."""
        port_combo = getattr(self._parent_window, "port_combo", None)
        port = port_combo.currentData() if port_combo else None
        if not port:
            QMessageBox.warning(
                self, "No device",
                "Could not resolve the serial port. Reconnect your Playdate."
            )
            return

        card = self._effective_card_image()
        if card is None:
            QMessageBox.warning(
                self, "Preview unavailable",
                "Launcher card image not ready."
            )
            return

        try:
            from src.core.icon_builder import compose_preview_framebuffer
            from src.core.transfer_engine import send_bitmap
            fb = compose_preview_framebuffer(card)
            import serial as _serial
            ser = _serial.Serial(port, 115200, timeout=2.0)
            try:
                send_bitmap(ser, fb)
                self._log("Preview pushed to device via bitmap.")
            finally:
                try:
                    ser.close()
                except Exception:
                    pass
        except Exception as e:
            self._log(f"Preview failed: {e!r}")
            QMessageBox.warning(
                self, "Preview failed",
                f"Could not send preview: {e!r}"
            )

    # --- Banner overrides ---

    def _flash_error_banner(self, text, duration_ms=2000):
        """Temporarily replace the connection banner with a red error
        message, then restore connection state."""
        # Set the red banner.
        self.banner.setText(text)
        self.banner.setStyleSheet(
            "QLabel { background-color: #c0392b; color: white;"
            " font-weight: bold; padding: 8px; }"
        )
        self.banner.setVisible(True)
        # Restore the connection-state banner after the delay.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(
            duration_ms,
            lambda: self._restore_connection_banner(),
        )

    def _restore_connection_banner(self):
        """Re-apply whatever the current connection state would display."""
        state = None
        if self._parent_window is not None and hasattr(
            self._parent_window, "current_connection_state"
        ):
            try:
                state = self._parent_window.current_connection_state()
            except Exception:
                state = None
        self._on_connection_changed(state)

    # --- Logging ---

    def _log(self, msg):
        self.log_view.append(msg)
        # Also keep the latest line visible.
        self.log_view.ensureCursorVisible()
        if self._log_callback:
            try:
                self._log_callback(msg)
            except Exception:
                pass

    def _log_device(self, line):
        """Append a raw protocol line received from the Playdate. Rendered
        in light grey so it's visually distinct from manager-emitted
        progress lines.
        """
        # Use HTML to colour just this line. We have to escape any HTML
        # metacharacters in the device line itself.
        from html import escape
        self.log_view.append(
            f"<span style='color: #888888;'>&lt;&lt; {escape(line)}</span>"
        )
        self.log_view.ensureCursorVisible()
        if self._log_callback:
            try:
                self._log_callback(f"<< {line}")
            except Exception:
                pass

    # --- Close handling ---

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            ret = QMessageBox.question(
                self, "Build in progress",
                "A forwarder is being built. Stop and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            try:
                self._worker.stop()
                self._worker.wait(3000)
            except Exception:
                pass
            self._worker = None
        # Wait for any in-flight card compose so Qt doesn't destroy a
        # running QThread. Best-effort: the network fetch already has
        # its own timeout, so this is bounded.
        worker = self._card_worker
        if worker is not None and worker.isRunning():
            try:
                worker.wait(6000)
            except Exception:
                pass
        super().closeEvent(event)
