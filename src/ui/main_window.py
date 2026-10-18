"""Main window for CrankBoy Manager."""

import sys
import time
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QCheckBox,
    QFileDialog, QProgressBar, QTextEdit, QMessageBox,
    QGroupBox, QApplication
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QAbstractAnimation, pyqtSignal
from PyQt6.QtGui import QIcon

from src.ui.file_list_widget import FileListWidget
from src.ui.forwarder_dialog import ForwarderDialog
from src.core.serial_worker import SerialWorker
from src.core.cover_update_worker import CoverUpdateWorker
from src.ui.cover_update_dialog import CoverUpdateDialog
from src.core.port_scanner_worker import PortScannerWorker
from src.core.cover_download_worker import CoverDownloadWorker
from src.core.transfer_engine import send_command, read_response, version_at_least
from src.core.constants import FileStatus, TransferButtonState, ArtStatus
from src.ui.spinner import Spinner
from src.version import VERSION


class MainWindow(QMainWindow):
    """Main application window."""

    # Emitted whenever the connection-status banner kind changes.
    # Payload is (kind, text, bg, fg) or None when no banner should show.
    connection_changed = pyqtSignal(object)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.worker = None
        self._cover_download_worker = None
        self._cover_update_worker = None
        self._cover_scan_worker = None
        self._cover_entries = None
        self._cover_done_crcs = set()
        self._cover_update_stopped = False
        self._draining_workers = []  # stopped workers whose port may still be busy
        self._transfer_stopped = False  # Track if transfer was stopped by user
        self._last_stop_time = 0  # Track when user last stopped transfer
        self._crankboy_connected = False  # Selected port has a running CrankBoy
        self._port_info = {}  # device path -> port dict from scan_for_crankboy
        self._last_scan_status = None  # Last scan status, used to dedupe log lines
        self._current_banner_kind = None  # Banner kind currently displayed
        self._banner_animation = None  # QPropertyAnimation for fold-up

        self.setWindowTitle(f"CrankBoy Manager {VERSION}")
        self.setMinimumSize(800, 700)

        # Set window icon
        self._set_window_icon()

        # Restore window geometry
        geometry = settings.get_window_geometry()
        if geometry:
            self.restoreGeometry(geometry)

        self._setup_ui()
        self._connect_signals()

        # Single-shot timer that triggers fold-up of the "Connected" banner.
        self._banner_hide_timer = QTimer(self)
        self._banner_hide_timer.setSingleShot(True)
        self._banner_hide_timer.timeout.connect(self._start_banner_fold)

        # Setup port scanner worker
        self._scanner_worker = PortScannerWorker()
        self._scanner_worker.scan_complete.connect(self._on_scan_complete)

        # Setup auto-refresh timer (3 seconds)
        self._scan_timer = QTimer()
        self._scan_timer.timeout.connect(self._start_port_scan)
        self._scan_timer.start(3000)

        # Initial state - delay first scan to allow UI to fully load
        self.port_combo.addItem("Initializing...", None)
        self.port_combo.setEnabled(False)
        self.scan_indicator.show()
        # Delay first scan by 500ms to allow UI to fully render
        QTimer.singleShot(500, self._start_port_scan)

        # Update button states
        self._update_transfer_button_state()
        self._update_clear_button_state()
        self._update_status_banner()

        # Match Art-column visibility to the saved "Download Cover Art" setting.
        self.file_list.setColumnHidden(
            self.file_list.COL_ART, not self.download_cover_cb.isChecked()
        )

    def _setup_ui(self):
        """Setup the user interface."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # === Top status banner ===
        self.status_banner = QLabel()
        self.status_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_banner.setVisible(False)
        layout.addWidget(self.status_banner)

        # === Controls (Settings) ===
        controls = QGroupBox("Settings")
        controls_layout = QHBoxLayout(controls)

        # Port selection
        controls_layout.addWidget(QLabel("Port:"))
        self.port_combo = QComboBox()
        # Ensure it sizes based on content
        self.port_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        controls_layout.addWidget(self.port_combo)

        # Scanning indicator (circular spinner)
        self.scan_indicator = Spinner(size=18)
        self.scan_indicator.hide()
        controls_layout.addWidget(self.scan_indicator)


        # Push options to the right
        controls_layout.addStretch()

        # Options (right-aligned)
        self.keep_compressed_cb = QCheckBox("Compress ROMs")
        self.keep_compressed_cb.setChecked(self.settings.get_keep_compressed())
        self.keep_compressed_cb.setToolTip("Store ROMs in compressed .gbz format on device")
        controls_layout.addWidget(self.keep_compressed_cb)


        self.restart_cb = QCheckBox("Restart After")
        self.restart_cb.setChecked(self.settings.get_auto_restart())
        self.restart_cb.setToolTip("Restart CrankBoy after all transfers are completed (allows the new games to be detected)")
        controls_layout.addWidget(self.restart_cb)

        self.download_cover_cb = QCheckBox("Download Cover Art")
        self.download_cover_cb.setChecked(self.settings.get_download_cover_art())
        self.download_cover_cb.setToolTip("Download cover art for each ROM and transfer it to the device")
        controls_layout.addWidget(self.download_cover_cb)

        layout.addWidget(controls)

        # === File List (with drag & drop) ===
        list_header = QHBoxLayout()
        list_header.addWidget(QLabel("Transfer Queue"))
        list_header.addStretch()

        self.add_btn = QPushButton("Add Files…")
        list_header.addWidget(self.add_btn)

        self.remove_btn = QPushButton("Remove Selected")
        self.remove_btn.setEnabled(False)  # Disabled until items are selected
        list_header.addWidget(self.remove_btn)

        self.forwarder_btn = QPushButton("Create Launcher…")
        self.forwarder_btn.setToolTip(
            "Open the launcher builder to wrap a ROM in a forwarder .pdx, launchable from the Playdate system menu directly"
        )
        list_header.addWidget(self.forwarder_btn)

        self.update_covers_btn = QPushButton("Update covers")
        self.update_covers_btn.setToolTip(
            "Refresh cover art for games installed on the device"
        )
        list_header.addWidget(self.update_covers_btn)

        layout.addLayout(list_header)

        self.file_list = FileListWidget()
        layout.addWidget(self.file_list, stretch=1)

        # === Action Buttons ===
        btn_layout = QHBoxLayout()

        self.transfer_btn = QPushButton(TransferButtonState.START.value)
        # Calculate minimum width based on longest button text
        self._update_transfer_button_width()
        btn_layout.addWidget(self.transfer_btn)

        btn_layout.addStretch()

        self.clear_btn = QPushButton("Clear Completed")
        btn_layout.addWidget(self.clear_btn)

        layout.addLayout(btn_layout)

        # === Status Log (Collapsible) ===
        log_header_layout = QHBoxLayout()

        self.log_toggle_btn = QPushButton("Show Log")
        log_header_layout.addWidget(self.log_toggle_btn)

        # Add stretch to push verbose checkbox to the right
        log_header_layout.addStretch()

        # Verbose checkbox (right-aligned, relates to log)
        self.verbose_cb = QCheckBox("Verbose")
        self.verbose_cb.setChecked(self.settings.get_verbose())
        log_header_layout.addWidget(self.verbose_cb)

        layout.addLayout(log_header_layout)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(100)
        layout.addWidget(self.log_view)

        # Connect toggle button
        self.log_toggle_btn.clicked.connect(self._toggle_log_visibility)

        # Restore log visibility from settings (default to hidden per user request)
        log_visible = getattr(self.settings, 'get_log_visible', lambda: False)()
        self._set_log_visibility(log_visible)

        # === Overall Progress ===
        progress_layout = QHBoxLayout()
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setValue(0)
        
        # Set initial styling for Linux/Windows (proper height and appearance)
        # macOS uses native styling
        if sys.platform != "darwin":
            self.overall_progress.setStyleSheet("""
                QProgressBar {
                    border: 1px solid palette(mid);
                    border-radius: 3px;
                    background: palette(base);
                    text-align: center;
                    min-height: 20px;
                }
                QProgressBar::chunk {
                    background: palette(highlight);
                }
            """)
        
        progress_layout.addWidget(self.overall_progress, stretch=1)
        self.progress_label = QLabel("0/0")
        progress_layout.addWidget(self.progress_label)
        layout.addLayout(progress_layout)

    def _connect_signals(self):
        """Connect UI signals."""
        self.add_btn.clicked.connect(self._add_files_dialog)
        self.remove_btn.clicked.connect(self._remove_selected_files)
        self.transfer_btn.clicked.connect(self._on_transfer_button_clicked)
        self.update_covers_btn.clicked.connect(self._on_update_covers_clicked)
        self.forwarder_btn.clicked.connect(self._on_create_forwarder_clicked)
        self.clear_btn.clicked.connect(self._clear_completed)

        self.file_list.files_added.connect(self._on_files_added)
        self.file_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.file_list.log_message.connect(self._log)
        self.file_list.delete_requested.connect(self._on_delete_requested)

        self.port_combo.currentIndexChanged.connect(self._on_port_selection_changed)
        self.download_cover_cb.toggled.connect(self._on_download_cover_toggled)

    def _set_window_icon(self):
        """Set the window icon from the bundled icon file."""
        from pathlib import Path

        # All icons are in src/assets/
        icon_dir = Path(__file__).parent.parent / "assets"

        # Determine icon file based on platform
        if sys.platform == "darwin":
            icon_file = "AppIcon.icns"
        elif sys.platform == "win32":
            icon_file = "AppIcon.ico"
        else:
            icon_file = "AppIcon.png"

        icon_path = icon_dir / icon_file

        # Also check for icon in PyInstaller bundle location
        if hasattr(sys, '_MEIPASS'):
            icon_path = Path(sys._MEIPASS) / "src" / "assets" / icon_file

        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _start_port_scan(self):
        """Start port scan in background thread."""
        # Skip if timer is stopped (shutting down)
        if not self._scan_timer.isActive():
            return
        # Skip if transfer is in progress or scan already running
        if self.worker and self.worker.isRunning():
            return
        if self._scanner_worker and self._scanner_worker.isRunning():
            return
        # Never probe the port while a serial session (cover listing/update
        # or a just-paused worker) may still be using it
        if self._serial_port_busy():
            return

        # Show scanning indicator if no CrankBoy is currently connected
        if not self._crankboy_connected:
            self.scan_indicator.show()

        # Start scan in background
        self._scanner_worker.start()

    def _on_scan_complete(self, result):
        """Handle scan completion from worker thread."""
        # Skip UI updates while any serial session is active or draining
        # (a scan started before a pause/transfer probes a busy port and
        # would corrupt the banner with a bogus "not connected" state)
        if self._serial_port_busy():
            return

        status = result['status']
        ports = result.get('ports', [])

        # Hide the scan indicator as soon as we have any Playdate to show.
        if ports:
            self.scan_indicator.hide()

        # Refresh our per-port info cache for the selection handler and banner.
        self._port_info = {p['device']: p for p in ports}

        # Save current selection to restore it if possible
        current_selection = self.port_combo.currentData()

        if ports:
            # Build the desired (device, label) list for the combo.
            new_items = [(p['device'], self._format_port_label(p)) for p in ports]

            current_items = []
            for i in range(self.port_combo.count()):
                data = self.port_combo.itemData(i)
                if data:
                    current_items.append((data, self.port_combo.itemText(i)))

            if current_items != new_items:
                # Block signals so rebuilding doesn't fire spurious selection changes.
                self.port_combo.blockSignals(True)
                self.port_combo.clear()
                for i, (device, label) in enumerate(new_items):
                    self.port_combo.addItem(label, device)
                    self.port_combo.setItemData(i, f"Device: {device}", Qt.ItemDataRole.ToolTipRole)

                # Restore prior selection if it still exists; otherwise prefer
                # the first port with a running CrankBoy.
                index = self.port_combo.findData(current_selection) if current_selection else -1
                if index < 0:
                    for i, (device, _label) in enumerate(new_items):
                        if self._port_info.get(device, {}).get('version'):
                            index = i
                            break
                if index < 0:
                    index = 0
                self.port_combo.setCurrentIndex(index)
                self.port_combo.blockSignals(False)

            self.port_combo.setEnabled(True)
            active_device = self.port_combo.currentData()
            if active_device:
                self.port_combo.setToolTip(f"Selected port: {active_device}")
        else:
            # No Playdate detected at all.
            self.port_combo.setEnabled(False)
            self.port_combo.setToolTip("")
            expected_text = "Playdate not connected"

            current_text = self.port_combo.currentText()
            if current_text != expected_text:
                self.port_combo.blockSignals(True)
                self.port_combo.clear()
                self.port_combo.addItem(expected_text, None)
                self.port_combo.blockSignals(False)

        # Update _crankboy_connected based on what's now selected.
        self._refresh_crankboy_connected()
        self._update_status_banner()

        # Log the scan message on status transitions, suppressing immediately
        # after the user stopped a transfer to avoid noisy bursts.
        if status != self._last_scan_status:
            if time.time() - self._last_stop_time > 3:
                self._log(result['message'])
            self._last_scan_status = status

        self._update_transfer_button_state()

    def _format_port_label(self, port):
        """Return the combo label for a port dict from scan_for_crankboy."""
        device = port['device']
        version = port.get('version')
        if version:
            return f"{device} (CrankBoy {version})"
        return device

    def _device_wants_cvi(self):
        """True unless the current target device is known to predate
        CrankBoy 3 (which reads pdi instead of cvi containers)."""
        device = self.port_combo.currentData()
        info = self._port_info.get(device) if device else None
        version = info.get('version') if info else None
        return True if not version else version_at_least(version, (3, 0, 0))

    def _refresh_crankboy_connected(self):
        """Recompute _crankboy_connected from the currently selected port.

        Transfer is only allowed when the selected port has a running CrankBoy
        AND the device is not stuck in a scene that prevents file transfer
        (in-game, settings menu, modal). Scene == None means the device didn't
        report it (older firmware) — we allow transfer in that case and rely
        on the pre-flight check in _start_transfer to catch problems.
        """
        device = self.port_combo.currentData()
        info = self._port_info.get(device) if device else None
        has_crankboy = bool(info and info.get('version'))
        in_blocking_scene = bool(info and self._is_blocking_scene(info.get('scene')))
        self._crankboy_connected = has_crankboy and not in_blocking_scene

    def _on_port_selection_changed(self, _index):
        """Handle user-driven port selection changes."""
        self._refresh_crankboy_connected()
        device = self.port_combo.currentData()
        if device:
            self.port_combo.setToolTip(f"Selected port: {device}")
        self._update_status_banner()
        self._update_transfer_button_state()

    # Scenes the manager can interact with cleanly. Anything else
    # (in-game, settings, credits, homebrew_hub, parental_lock, ...) is
    # treated as a "wrong scene" -- both for ROM transfers and for the
    # forwarder wizard. sft-modal is included so the banner stays
    # "Connected" during an active file transfer (which the transfer
    # worker drives the device into via cb:sft:on).
    _NON_BLOCKING_SCENES = frozenset({"library", "sft-modal"})

    def _is_blocking_scene(self, scene):
        """True if the given scene id means the manager can't interact.

        We allowlist non-blocking scenes; an unknown / None scene from
        older firmware is treated as non-blocking so that legacy
        devices remain usable.
        """
        if scene is None:
            return False
        return scene not in self._NON_BLOCKING_SCENES

    def _compute_banner_kind(self):
        """Determine which banner (if any) should be shown."""
        if not self._port_info:
            return 'no_playdate'
        device = self.port_combo.currentData()
        info = self._port_info.get(device) if device else None
        if info is None:
            return None
        if not info.get('accessible', True):
            return 'inaccessible'
        if not info.get('version'):
            return 'launch'
        if self._is_blocking_scene(info.get('scene')):
            return 'wrong_scene'
        return 'connected'

    _BANNER_STYLES = {
        'no_playdate': ("Connect and unlock your Playdate", "#ffc500", "black"),
        'inaccessible': ("Failed to communicate with Playdate. See log.", "#ff0000", "white"),
        'launch': ("Please launch CrankBoy on your playdate", "#7700ff", "white"),
        'wrong_scene': ("Return to the Library view to transfer ROMs", "#7700ff", "white"),
        'connected': ("Connected", "#1fc54e", "white"),
    }

    def _update_status_banner(self):
        """Update the top-of-window status banner based on current port state."""
        kind = self._compute_banner_kind()
        if kind == self._current_banner_kind:
            # No change. Leave whatever the banner is currently doing
            # (visible, folded, mid-animation) alone.
            return

        # Cancel any in-flight fold animation or pending hide timer.
        self._cancel_banner_animation()

        self._current_banner_kind = kind

        if kind is None:
            self.status_banner.setVisible(False)
            self.connection_changed.emit(None)
            return

        text, bg, fg = self._BANNER_STYLES[kind]
        self._show_status_banner(text, bg, fg)

        if kind == 'connected':
            # After 3 seconds, fold the banner away.
            self._banner_hide_timer.start(3000)

        self.connection_changed.emit((kind, text, bg, fg))

    def current_connection_state(self):
        """Return (kind, text, bg, fg) for the current banner kind, or
        None when no banner is active. Used by satellite windows (e.g.
        the forwarder dialog) to mirror connection status.
        """
        kind = self._current_banner_kind
        if kind is None:
            return None
        text, bg, fg = self._BANNER_STYLES[kind]
        return (kind, text, bg, fg)

    def current_port_info(self):
        """Return the selected port's info dict, or None."""
        device = self.port_combo.currentData()
        if not device:
            return None
        return self._port_info.get(device)

    def _show_status_banner(self, text, bg, fg):
        """Show the status banner with the given text and colors."""
        # Reset any leftover height constraint from a prior fold animation.
        self.status_banner.setMaximumHeight(16777215)
        self.status_banner.setText(text)
        self.status_banner.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: {fg};"
            f" font-weight: bold; padding: 8px; }}"
        )
        self.status_banner.setVisible(True)

    def _cancel_banner_animation(self):
        """Stop any pending hide timer or fold animation and restore height."""
        if self._banner_hide_timer.isActive():
            self._banner_hide_timer.stop()
        if self._banner_animation is not None:
            if self._banner_animation.state() == QAbstractAnimation.State.Running:
                self._banner_animation.stop()
            self._banner_animation = None
        self.status_banner.setMaximumHeight(16777215)

    def _start_banner_fold(self):
        """Animate the banner folding up over 300ms, then hide it."""
        start_h = self.status_banner.sizeHint().height()
        if start_h <= 0:
            self.status_banner.setVisible(False)
            return
        self._banner_animation = QPropertyAnimation(self.status_banner, b"maximumHeight")
        self._banner_animation.setDuration(300)
        self._banner_animation.setStartValue(start_h)
        self._banner_animation.setEndValue(0)
        self._banner_animation.finished.connect(self._on_banner_folded)
        self._banner_animation.start()

    def _on_banner_folded(self):
        """Called when the fold animation finishes."""
        self.status_banner.setVisible(False)
        self.status_banner.setMaximumHeight(16777215)
        self._banner_animation = None

    def _add_files_dialog(self):
        """Open file dialog to add files."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select ROM Files",
            "",
            "Game Boy ROMs (*.gb *.gbc *.gbz);;"
            "Archives (*.zip *.tar *.tar.gz *.tgz *.tar.bz2 *.tbz2 *.tar.xz *.txz);;"
            "All Files (*.*)"
        )
        if files:
            self.file_list.add_files(files)

    def _on_files_added(self, files):
        """Handle files being added."""
        self._log(f"Added {len(files)} file(s)")
        self._update_progress_label()
        self._update_transfer_button_state()
        self._update_clear_button_state()

        # Start background cover downloads for new files
        self._start_cover_downloads_for_files(files)

    def _start_cover_downloads_for_files(self, filepaths):
        """Start background cover downloads for the given file paths."""
        if not filepaths:
            return

        # Honour the user's "Download Cover Art" preference: do not even
        # attempt downloads when the checkbox is off. The Art column will
        # still show CRC-based Match/No Match info from when rows were added.
        if not self.download_cover_cb.isChecked():
            return

        # Get references to EXISTING file_info objects from file_list
        # This ensures cover data is stored in the same objects used during transfer
        file_info_list = []
        for filepath in filepaths:
            file_info = self.file_list.files_info.get(filepath)
            if file_info and file_info.get('original_crc'):
                # Skip if cover already downloaded
                if file_info.get('cover_data') is not None:
                    continue
                file_info_list.append(file_info)

        if not file_info_list:
            return

        # Create worker lazily if needed
        if self._cover_download_worker is None:
            # cvi containers are the default (CrankBoy 3+); prefetch steps
            # down to pdi only when a connected device is known to predate
            # 3.0. If the device connects later, the transfer step aligns
            # the format per file.
            device = self.port_combo.currentData()
            info = self._port_info.get(device) if device else None
            version = info.get('version') if info else None
            use_cvi = True if not version else version_at_least(version, (3, 0, 0))
            if not use_cvi:
                self._log("  Downloading covers as pdi (CrankBoy <3 device detected)")
            self._cover_download_worker = CoverDownloadWorker(max_concurrent=20, use_cvi=use_cvi)
            self._cover_download_worker.cover_started.connect(self._on_cover_download_started)
            self._cover_download_worker.cover_completed.connect(self._on_cover_download_completed)
            self._cover_download_worker.all_completed.connect(self._on_all_covers_completed)
            self._cover_download_worker.start()

        # Add files to worker's queue
        added_count = self._cover_download_worker.add_to_queue(file_info_list)
        if added_count > 0:
            self._log(f"  Queued {added_count} cover download(s)...")

    def _on_cover_download_started(self, rom_filename, cover_filename):
        """Handle cover download starting."""
        self._log(f"    Downloading cover for {rom_filename}...")

    def _on_cover_download_completed(self, rom_filename, success, message):
        """Handle cover download completion."""
        if success:
            self._log(f"    ✓ Cover ready for {rom_filename}")
        else:
            self._log(f"    Cover not available for {rom_filename}: {message}")

        # Update the Art column for the matching row.
        filepath = self._find_filepath_by_filename(rom_filename)
        if filepath is None:
            return
        if success:
            self.file_list.set_art_status(filepath, ArtStatus.OK)
        else:
            # "Not in database" comes from the worker when the CRC has no
            # cover entry. Keep that as No Match rather than Failed.
            if "Not in database" in (message or ""):
                self.file_list.set_art_status(filepath, ArtStatus.NO_MATCH)
            else:
                self.file_list.set_art_status(filepath, ArtStatus.FAILED)

    def _find_filepath_by_filename(self, rom_filename):
        """Map a ROM filename (basename) back to its full path in the list."""
        for filepath, info in self.file_list.files_info.items():
            if info.get('filename') == rom_filename:
                return filepath
        return None

    def _on_all_covers_completed(self):
        """Handle all cover downloads completing (queue empty)."""
        self._log("  All cover downloads completed")

    def _on_download_cover_toggled(self, checked):
        """When the user toggles Download Cover Art, show/hide the Art column
        and (if enabled) kick off any pending downloads."""
        self.settings.set_download_cover_art(checked)
        self.file_list.setColumnHidden(self.file_list.COL_ART, not checked)
        if not checked:
            return
        # Find files that have a database Match but no cover data yet.
        pending = []
        for filepath in self.file_list.filepaths:
            art = self.file_list.get_art_status(filepath)
            info = self.file_list.files_info.get(filepath)
            if art == ArtStatus.MATCH and info and info.get('cover_data') is None:
                pending.append(filepath)
        if pending:
            self._start_cover_downloads_for_files(pending)

    def _on_selection_changed(self):
        """Handle selection change in file list."""
        # Only enable remove button if not currently transferring
        is_transferring = self.worker is not None and self.worker.isRunning()
        has_selection = len(self.file_list.selectedItems()) > 0
        self.remove_btn.setEnabled(has_selection and not is_transferring)

    def _on_delete_requested(self):
        """Handle Delete/Backspace from the file list — same gating as the button."""
        if self.remove_btn.isEnabled():
            self._remove_selected_files()

    def _remove_selected_files(self):
        """Remove selected files from the list."""
        # Get selected rows (avoid duplicates from multiple columns)
        selected_rows = set()
        for item in self.file_list.selectedItems():
            selected_rows.add(item.row())

        # Remove files in reverse order to maintain row indices
        for row in sorted(selected_rows, reverse=True):
            if row < len(self.file_list.filepaths):
                filepath = self.file_list.filepaths[row]
                self.file_list.remove_file(filepath)

        self._update_progress_label()
        self._update_transfer_button_state()
        self._update_clear_button_state()

    def _start_transfer(self):
        """Start the transfer process."""
        # Get current keep_compressed setting
        keep_compressed = self.keep_compressed_cb.isChecked()

        # Filter to only pending or failed files, using existing file_info with cover data
        files = []
        for filepath in self.file_list.filepaths:
            row = self.file_list.filepaths.index(filepath)
            status_item = self.file_list.item(row, self.file_list.COL_STATUS)  # Status column
            if status_item:
                status = status_item.data(Qt.ItemDataRole.UserRole)
                # Only include files that are not done
                if status != FileStatus.DONE:
                    # Get existing file_info (which has cover data from background download)
                    file_info = self.file_list.files_info.get(filepath)
                    if file_info:
                        if file_info.get('is_device_entry'):
                            continue
                        # Update keep_compressed setting in the existing file_info
                        if keep_compressed:
                            # Keep as GBZ on device - don't provide original info
                            file_info['original_filename'] = None
                            file_info['original_crc'] = None
                        files.append(file_info)

        if not files:
            return

        port = self.port_combo.currentData()
        if not port:
            return

        # Query current scene to ensure we're in library
        try:
            import serial
            self._log("Checking device state...")
            ser = serial.Serial(port, 115200, timeout=2)
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            # Send scene query
            send_command(ser, "cb:scene:get")

            # Wait for response with timeout
            response = read_response(ser, timeout=2.0)
            self._log(f"DEBUG: Raw response: {repr(response)}")
            scene_type = None
            if response and response.startswith("cb:scene:"):
                # Split by colon (response is already stripped by read_response)
                parts = response.split(":")
                self._log(f"DEBUG: Split parts: {parts}")
                scene_type = parts[2] if len(parts) >= 3 else None

            ser.close()

            if scene_type == "sft-modal":
                # SFT overlay already active, just continue with transfer
                self._log("File Transfer Mode already active, continuing transfer...")
            elif scene_type != "library":
                self._log(f"Device not in library scene (current: {scene_type or 'unknown'})")
                QMessageBox.warning(self, "Wrong Scene", "Please open the Game Library to transfer ROMs.")
                return
            else:
                # Enable SFT overlay
                self._log("Enabling File Transfer Mode...")
                ser = serial.Serial(port, 115200, timeout=2)
                send_command(ser, "cb:sft:on")
                response = read_response(ser, timeout=2.0)
                ser.close()

                # Clean response for comparison
                response_clean = response

                if response_clean == "cb:sft:ok":
                    self._log("File Transfer Mode enabled ✓")
                elif response_clean == "cb:sft:error:already-active":
                    self._log("File Transfer Mode already active")
                else:
                    self._log(f"Failed to enable File Transfer Mode: {response_clean}")
                    QMessageBox.warning(self, "Transfer Error", f"Failed to enable transfer mode: {response_clean}")
                    return

        except Exception as e:
            self._log(f"Error checking device state: {e}")
            QMessageBox.warning(self, "Transfer Error", f"Failed to communicate with device: {e}")
            return

        # Save settings
        self.settings.set_verbose(self.verbose_cb.isChecked())
        self.settings.set_auto_restart(self.restart_cb.isChecked())
        self.settings.set_keep_compressed(self.keep_compressed_cb.isChecked())
        self.settings.set_download_cover_art(self.download_cover_cb.isChecked())

        # Track overall progress (only for files being transferred)
        self._files_to_transfer = files
        self._current_file_index = 0
        # Include cover data size in total if present and covers are enabled
        include_covers = self.download_cover_cb.isChecked()
        self._total_bytes_all_files = sum(
            f['gbz_size'] + (len(f['cover_data']) if include_covers and f.get('cover_data') else 0)
            for f in files
        )
        self._bytes_completed = 0
        self._current_file_bytes = 0
        self._current_file_total = 0

        # Count skipped files (those with "Done" status)
        done_count = 0
        for row in range(self.file_list.rowCount()):
            status_item = self.file_list.item(row, self.file_list.COL_STATUS)
            if status_item and status_item.data(Qt.ItemDataRole.UserRole) == FileStatus.DONE:
                done_count += 1

        if done_count > 0:
            self._log(f"Skipping {done_count} already completed file(s)")

        # Disable controls during transfer
        self._set_controls_enabled(False)

        # Stop auto-refresh during transfer
        self._scan_timer.stop()

        # Reset stopped flag when starting new transfer
        self._transfer_stopped = False

        # Create and start worker
        options = {
            'verbose': self.verbose_cb.isChecked(),
            'restart': self.restart_cb.isChecked(),
            'use_sft': True,  # We enabled SFT overlay before starting
            'download_cover_art': self.download_cover_cb.isChecked(),
            'use_cvi': self._device_wants_cvi(),
        }

        self.worker = SerialWorker(port, files, options)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_progress.connect(self._on_file_progress)
        self.worker.file_completed.connect(self._on_file_completed)
        self.worker.chunk_sent.connect(self._on_chunk_sent)
        self.worker.log_message.connect(self._log)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.all_completed.connect(self._on_all_completed)
        self.worker.cover_started.connect(self._on_cover_started)
        self.worker.cover_completed.connect(self._on_cover_completed)

        self._log("Starting transfer...")
        self.overall_progress.setValue(0)
        # Button state will be updated by _update_transfer_button_state
        # Set custom style on Linux/Windows to fix color issues
        # macOS uses native styling which looks better
        if sys.platform != "darwin":
            self.overall_progress.setStyleSheet("""
                QProgressBar {
                    border: 1px solid #48f;
                    border-radius: 3px;
                    background: palette(base);
                    text-align: center;
                }
                QProgressBar::chunk {
                    background: #48f;
                }
            """)
        self.worker.start()
        # Update button to show "Stop Transfer"
        self._update_transfer_button_state()

    def _stop_transfer(self):
        """Pause the running transfer; the queue stays resumable."""
        import time
        if self.worker:
            self._log("Pausing transfer...")
            self._transfer_stopped = True
            self._last_stop_time = time.time()
            worker = self.worker
            self.worker = None
            # the thread can need a few seconds (serial read timeouts) to
            # notice the stop; never destroy it while it is still running
            worker.finished.connect(self._on_drain_finished)
            self._draining_workers.append(worker)
            worker.stop()

        # Mark all non-done files as Pending for resume
        for filepath in self.file_list.filepaths:
            row = self.file_list.filepaths.index(filepath)
            status_item = self.file_list.item(row, self.file_list.COL_STATUS)
            if status_item and status_item.data(Qt.ItemDataRole.UserRole) != FileStatus.DONE:
                self.file_list.set_file_status(filepath, FileStatus.PENDING)
                # Reset progress bar to 0
                progress_widget = self.file_list.cellWidget(row, self.file_list.COL_PROGRESS)
                if progress_widget and hasattr(progress_widget, 'progress_bar'):
                    progress_widget.progress_bar.setValue(0)
        
        # Update button state to show Resume
        self._update_transfer_button_state()

    def _on_drain_finished(self):
        """A paused worker fully exited; its serial port is free again."""
        worker = self.sender()
        if worker in self._draining_workers:
            self._draining_workers.remove(worker)
        worker.deleteLater()

    def _serial_port_busy(self):
        """True while any serial session (running or draining) holds the port."""
        if self.worker and self.worker.isRunning():
            return True
        if self._cover_update_worker and self._cover_update_worker.isRunning():
            return True
        if self._cover_scan_worker and self._cover_scan_worker.isRunning():
            return True
        return any(w.isRunning() for w in self._draining_workers)

    def _on_transfer_button_clicked(self):
        """Handle transfer button click - start or stop based on current state."""
        if self.worker and self.worker.isRunning():
            # Transfer is running, stop it
            self._stop_transfer()
        elif self._cover_update_worker and self._cover_update_worker.isRunning():
            self._stop_cover_update()
        elif (self._cover_entries and not self._has_rom_files_pending()
                and (self._has_failed_files() or self._has_pending_cover_rows())):
            # retry/resume the last cover selection (only not-Done rows)
            self._start_cover_update(self._cover_entries, skip_done=True)
        else:
            # No transfer running, start one
            if self._serial_port_busy():
                self._log("Serial port is still busy, try again in a moment")
                return
            self._start_transfer()

    def _on_create_forwarder_clicked(self):
        """Open the standalone forwarder builder dialog.

        The dialog is openable even with no CrankBoy connected; it
        mirrors connection status via the connection_changed signal and
        greys its own "Add to Device" button until the device is ready.
        """
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(
                self, "Busy", "Wait for the current operation to finish."
            )
            return

        dlg = ForwarderDialog(self, log_callback=self._log)
        dlg.worker_started.connect(self._on_forwarder_dialog_worker_started)
        dlg.worker_finished.connect(self._on_forwarder_dialog_worker_finished)
        dlg.show()

    def _on_forwarder_dialog_worker_started(self):
        """Pause scanning while a forwarder build runs (data-disk mode)."""
        self._scan_timer.stop()

    def _on_forwarder_dialog_worker_finished(self):
        """Resume scanning once the forwarder build is done."""
        if not (self.worker and self.worker.isRunning()):
            self._scan_timer.start(3000)

    def _clear_completed(self):
        """Clear completed files from the list."""
        self.file_list.clear_completed()
        self._update_progress_label()
        self._update_transfer_button_state()
        self._update_clear_button_state()

    def _on_file_started(self, filename, total_bytes):
        """Handle file transfer starting."""
        filepath = self._find_filepath_by_name(filename)
        if filepath:
            self.file_list.mark_transferring(filepath)
            self._current_file_total = total_bytes
            self._current_file_bytes = 0
            self._current_file_index += 1
            self._update_progress_label()

    def _on_file_progress(self, bytes_sent, total_bytes):
        """Handle file progress update."""
        # Update individual file progress
        current_file = self._get_current_transferring_file()
        if current_file:
            progress = int((bytes_sent / total_bytes) * 100)
            self.file_list.set_file_progress(current_file, bytes_sent, total_bytes)

        # Update overall progress (bytes-based; cover sessions keep the
        # count-based value the worker emits)
        self._current_file_bytes = bytes_sent
        total_sent = self._bytes_completed + self._current_file_bytes
        if self._total_bytes_all_files > 0:
            overall_progress = int((total_sent / self._total_bytes_all_files) * 100)
            self.overall_progress.setValue(overall_progress)

    def _on_file_completed(self, filename, success, message):
        """Handle file transfer completion."""
        filepath = self._find_filepath_by_name(filename)
        if filepath:
            is_user_stopped = message == "User stopped"
            if is_user_stopped:
                self.file_list.set_file_status(filepath, FileStatus.PENDING)
                self.file_list.set_file_progress(filepath, 0, 100)
            elif message.startswith("No cover"):
                # cover lookup/download found nothing to send: not an error
                self.file_list.set_file_status(filepath, FileStatus.UNAVAILABLE)
            else:
                self.file_list.set_file_status(filepath, FileStatus.DONE if success else FileStatus.FAILED)
                self.file_list.set_file_progress(filepath, 100, 100, is_error=not success)
                if success:
                    info = self.file_list.files_info.get(filepath)
                    if info and info.get('is_device_entry') and info.get('original_crc'):
                        self._cover_done_crcs.add(info['original_crc'])
            self._update_clear_button_state()

        # Add to completed bytes for overall progress
        self._bytes_completed += self._current_file_total
        self._current_file_bytes = 0

        # Choose status symbol based on outcome
        if is_user_stopped:
            status = "⏸"
        elif success:
            status = "✓"
        else:
            status = "✗"
        self._log(f"{status} {filename}: {message}")

    def _on_chunk_sent(self, chunk_num):
        """Handle chunk sent."""
        # Could update per-chunk progress here
        pass

    def _on_cover_started(self, cover_filename, total_bytes):
        """Handle cover art transfer starting."""
        self._log(f"  Transferring cover to device: {cover_filename}")

    def _on_cover_completed(self, cover_filename, success, message):
        """Handle cover art transfer completion."""
        if success:
            self._log(f"  ✓ Cover saved: {cover_filename}")
        else:
            self._log(f"  Cover download failed: {message}")

    def _on_error(self, filename, error):
        """Handle transfer error."""
        self._log(f"Error: {error}")

    def _on_update_covers_clicked(self):
        """Open the selective cover-update dialog."""
        if self._cover_update_worker and self._cover_update_worker.isRunning():
            return
        if self._serial_port_busy():
            self._log("Serial port is still busy, try again in a moment")
            return
        if self.worker and self.worker.isRunning():
            self._log("Cannot update covers while a transfer is running")
            return
        device = self.port_combo.currentData()
        options = {
            'verbose': self.verbose_cb.isChecked(),
            'use_sft': False,
            'download_cover_art': True,
            'use_cvi': self._device_wants_cvi(),
        }
        dialog = CoverUpdateDialog(self, device, options, log_callback=self._log)
        dialog.start_requested.connect(self._start_cover_update)
        dialog.scan_finished.connect(self._on_cover_scan_finished)
        # track from the start so port guards cover the listing phase
        self._cover_scan_worker = dialog.scan_worker()
        self._scan_timer.stop()
        dialog.exec()
        # a canceled scan keeps draining for a few seconds; track it so
        # transfers wait until the port is free again
        if dialog.scan_worker() and dialog.scan_worker().isRunning():
            self._cover_scan_worker = dialog.scan_worker()
        if not self._cover_update_worker:
            self._scan_timer.start(3000)

    def _on_cover_scan_finished(self):
        """Scan worker exited; the serial port is free again."""
        self._cover_scan_worker = None

    def _start_cover_update(self, entries, skip_done=False):
        """Transfer covers for the dialog-selected games, then refresh.

        entries: dicts with 'crc32' (int) + 'rom_name' (device ROM name).
        Selected games appear as rows in the transfer queue and stream
        per-file progress through the standard handlers. skip_done
        (resume/retry) re-runs only rows that are not Done.
        """
        self._cover_update_stopped = False
        if skip_done:
            # resume: only what has not succeeded yet (survives row removal
            # by Clear Completed)
            entries = [e for e in entries if e['crc32'] not in self._cover_done_crcs]
            if not entries:
                self._log("Nothing left to update")
                return
        else:
            self._cover_done_crcs = set()  # fresh selection: re-transfer all
        if self._serial_port_busy():
            self._log("Serial port is still busy, try again in a moment")
            return
        device = self.port_combo.currentData()

        self.file_list.add_device_cover_entries(
            [
                {
                    'device_filepath': f"device:{e['rom_name']}",
                    'rom_name': e['rom_name'],
                    'crc32': e['crc32'],
                }
                for e in entries
            ]
        )
        self._total_bytes_all_files = 0  # overall bar is count-based for covers
        self._files_to_transfer = []
        self._current_file_index = 0
        self._bytes_completed = 0
        self._current_file_bytes = 0
        self._current_file_total = 0
        self._set_controls_enabled(False)
        self._scan_timer.stop()

        options = {
            'verbose': self.verbose_cb.isChecked(),
            'use_sft': False,
            'download_cover_art': True,
            'use_cvi': self._device_wants_cvi(),
        }
        self._cover_update_worker = CoverUpdateWorker(
            device, options, selected_crcs={e['crc32'] for e in entries}
        )
        self._cover_entries = entries
        self._cover_update_worker.log_message.connect(self._log)
        self._cover_update_worker.error_occurred.connect(self._on_error)
        self._cover_update_worker.file_started.connect(self._on_file_started)
        self._cover_update_worker.file_progress.connect(self._on_file_progress)
        self._cover_update_worker.file_completed.connect(self._on_file_completed)
        self._cover_update_worker.cover_started.connect(self._on_cover_started)
        self._cover_update_worker.cover_completed.connect(self._on_cover_completed)
        self._cover_update_worker.cover_overall.connect(self.overall_progress.setValue)
        self._cover_update_worker.all_completed.connect(self._on_update_covers_completed)
        self._cover_update_worker.start()
        self._update_transfer_button_state()

    def _stop_cover_update(self):
        """Pause the running cover update (the transfer button's Pause)."""
        self._log("Pausing cover update...")
        self._cover_update_stopped = True
        worker = self._cover_update_worker
        self._cover_update_worker = None
        worker.finished.connect(self._on_drain_finished)
        self._draining_workers.append(worker)
        worker.stop()

    def _on_update_covers_completed(self, ok):
        self._set_controls_enabled(True)
        self._scan_timer.start(3000)
        if ok:
            self._log("Cover update finished")
            self.overall_progress.setValue(100)
        elif self._cover_update_stopped or self._has_pending_cover_rows():
            self._log("Cover update stopped")
        else:
            self._log("Cover update FAILED")
        # thread may still be a few statements from run() returning; defer
        # its destruction until it has actually finished
        worker = self._cover_update_worker
        self._cover_update_worker = None
        if worker:
            worker.finished.connect(self._on_drain_finished)
            self._draining_workers.append(worker)
        self._update_transfer_button_state()

    def _on_all_completed(self, all_successful):
        """Handle all transfers completing."""
        self._set_controls_enabled(True)

        # Re-enable remove button if items are selected
        has_selection = len(self.file_list.selectedItems()) > 0
        self.remove_btn.setEnabled(has_selection)

        if all_successful:
            self._log("All transfers completed successfully!")
            self.overall_progress.setValue(100)
            # Set green style for successful completion (Linux/Windows only)
            if sys.platform != "darwin":
                self.overall_progress.setStyleSheet("""
                    QProgressBar {
                        border: 1px solid #4a4;
                        border-radius: 3px;
                        background: palette(base);
                        text-align: center;
                    }
                    QProgressBar::chunk {
                        background: #4a4;
                    }
                """)
        elif self._transfer_stopped:
            self._log("Transfer stopped by user.")
            # Set red style for stopped transfer (Linux/Windows only)
            if sys.platform != "darwin":
                self.overall_progress.setStyleSheet("""
                    QProgressBar {
                        border: 1px solid #c44;
                        border-radius: 3px;
                        background: palette(base);
                        text-align: center;
                    }
                    QProgressBar::chunk {
                        background: #c44;
                    }
                """)
        else:
            self._log("Some transfers failed.")
            # Set red style for failed transfers (Linux/Windows only)
            if sys.platform != "darwin":
                self.overall_progress.setStyleSheet("""
                    QProgressBar {
                        border: 1px solid #c44;
                        border-radius: 3px;
                        background: palette(base);
                        text-align: center;
                    }
                    QProgressBar::chunk {
                        background: #c44;
                    }
                """)

        # thread may still be a few statements from run() returning; defer
        # its destruction until it has actually finished
        worker = self.worker
        self.worker = None
        if worker:
            worker.finished.connect(self._on_drain_finished)
            self._draining_workers.append(worker)

        # Reset transfer tracking and update label
        self._files_to_transfer = []
        self._current_file_index = 0
        self._update_progress_label()
        self._update_transfer_button_state()
        self._update_clear_button_state()

        # Restart auto-refresh timer
        self._scan_timer.start(3000)

    def _log(self, message):
        """Add message to log."""
        self.log_view.append(message)

    def _find_filepath_by_name(self, filename):
        """Find filepath in file list by filename.

        A local file and a device cover row can share a basename; prefer
        the row belonging to whichever worker is currently emitting.
        """
        prefer_device = bool(
            self._cover_update_worker and self._cover_update_worker.isRunning()
        )
        fallback = None
        for filepath, info in self.file_list.files_info.items():
            if info['filename'] != filename:
                continue
            if bool(info.get('is_device_entry')) == prefer_device:
                return filepath
            if fallback is None:
                fallback = filepath
        return fallback

    def _get_current_transferring_file(self):
        """Get the filepath of the file currently being transferred."""
        for row, filepath in enumerate(self.file_list.filepaths):
            status_item = self.file_list.item(row, self.file_list.COL_STATUS)
            if status_item:
                status = status_item.data(Qt.ItemDataRole.UserRole)
                if status == FileStatus.TRANSFERRING:
                    return filepath
        return None

    def _set_controls_enabled(self, enabled):
        """Enable/disable controls during transfer."""
        self.add_btn.setEnabled(enabled)
        # Keep file list enabled so users can see progress
        self.port_combo.setEnabled(enabled)
        self.verbose_cb.setEnabled(enabled)
        self.restart_cb.setEnabled(enabled)
        self.keep_compressed_cb.setEnabled(enabled)
        self.download_cover_cb.setEnabled(enabled)
        self.forwarder_btn.setEnabled(enabled)
        # Transfer button stays enabled (text changes to Stop/Start)
        self.clear_btn.setEnabled(enabled)
        if enabled:
            self._update_clear_button_state()

        # Remove button is disabled during transfer
        self.remove_btn.setEnabled(False)

    def _update_progress_label(self):
        """Update the progress label."""
        # During transfer, show current/total format
        if hasattr(self, '_files_to_transfer') and self._files_to_transfer:
            total = len(self._files_to_transfer)
            current = getattr(self, '_current_file_index', 0)
            self.progress_label.setText(f"{current}/{total}")
            return

        files = self.file_list.get_files()
        count = len(files)
        if count == 0:
            self.progress_label.setText("0 files")
        elif count == 1:
            self.progress_label.setText("1 file")
        else:
            self.progress_label.setText(f"{count} files")

    def _has_transferable_files(self):
        """Check if there are any files in the queue that are not 'Done'."""
        for row in range(self.file_list.rowCount()):
            status_item = self.file_list.item(row, self.file_list.COL_STATUS)
            if status_item:
                status = status_item.data(Qt.ItemDataRole.UserRole)
                if status != FileStatus.DONE:
                    return True
        return False

    def _update_transfer_button_width(self):
        """Calculate and set minimum width based on longest button text."""
        from PyQt6.QtGui import QFontMetrics
        
        texts = [state.value for state in TransferButtonState]
        fm = QFontMetrics(self.transfer_btn.font())
        max_width = max(fm.horizontalAdvance(text) for text in texts)
        # Add padding for margins
        self.transfer_btn.setMinimumWidth(max_width + 20)

    def _has_rom_files_pending(self):
        """ROM rows (device cover rows excluded) that are not Done."""
        for row in range(self.file_list.rowCount()):
            status_item = self.file_list.item(row, self.file_list.COL_STATUS)
            if not status_item:
                continue
            status = status_item.data(Qt.ItemDataRole.UserRole)
            if status == FileStatus.DONE:
                continue
            filepath = self.file_list.filepaths[row]
            info = self.file_list.files_info.get(filepath)
            if info and not info.get('is_device_entry'):
                return True
        return False

    def _has_pending_cover_rows(self):
        """True when any device cover row is still not Done."""
        for row in range(self.file_list.rowCount()):
            filepath = self.file_list.filepaths[row]
            info = self.file_list.files_info.get(filepath)
            if not info or not info.get('is_device_entry'):
                continue
            status_item = self.file_list.item(row, self.file_list.COL_STATUS)
            if status_item and status_item.data(Qt.ItemDataRole.UserRole) not in (
                FileStatus.DONE,
                FileStatus.UNAVAILABLE,
            ):
                return True
        return False

    def _has_failed_files(self):
        """Check if there are any failed files in the list."""
        for row in range(self.file_list.rowCount()):
            status_item = self.file_list.item(row, self.file_list.COL_STATUS)
            if status_item and status_item.data(Qt.ItemDataRole.UserRole) == FileStatus.FAILED:
                return True
        return False

    def _update_transfer_button_state(self):
        """Update transfer button text and enabled state based on current state."""
        self.update_covers_btn.setEnabled(
            self._crankboy_connected
            and self._device_wants_cvi()
            and not self._serial_port_busy()
        )
        # cover update running: the transfer button is its Stop button
        if self._cover_update_worker and self._cover_update_worker.isRunning():
            self.transfer_btn.setText(TransferButtonState.STOP.value)
            self.transfer_btn.setEnabled(True)
            return
        # failed cover session + no ROM work pending: retry the last selection
        if (self._cover_entries and self._has_failed_files()
                and not self._has_rom_files_pending()):
            self.transfer_btn.setText(TransferButtonState.RETRY.value)
            self.transfer_btn.setEnabled(self._crankboy_connected)
            return
        # stopped cover session with rows left: offer resume
        if (self._cover_entries and not self._has_rom_files_pending()
                and self._has_pending_cover_rows()):
            self.transfer_btn.setText(TransferButtonState.RESUME.value)
            self.transfer_btn.setEnabled(self._crankboy_connected)
            return
        # If a transfer is already running, the button should be enabled (it's the "Stop" button)
        if self.worker and self.worker.isRunning():
            self.transfer_btn.setText(TransferButtonState.STOP.value)
            self.transfer_btn.setEnabled(True)
            return

        has_files = self._has_transferable_files()
        
        if not has_files:
            # No files - reset to Start Transfer and clear stopped flag
            self.transfer_btn.setText(TransferButtonState.START.value)
            self.transfer_btn.setEnabled(False)
            self._transfer_stopped = False
        elif self._transfer_stopped:
            # Previously stopped - check if retry or resume needed
            has_failed = self._has_failed_files()
            if has_failed:
                self.transfer_btn.setText(TransferButtonState.RETRY.value)
            else:
                self.transfer_btn.setText(TransferButtonState.RESUME.value)
            self.transfer_btn.setEnabled(self._crankboy_connected)
        else:
            # Fresh start
            self.transfer_btn.setText(TransferButtonState.START.value)
            self.transfer_btn.setEnabled(self._crankboy_connected)

    def _update_clear_button_state(self):
        """Update the enabled state of the clear button."""
        # While any serial session (running or draining) is active, the
        # button stays disabled
        if self._serial_port_busy():
            self.clear_btn.setEnabled(False)
            return

        has_done = False
        for row in range(self.file_list.rowCount()):
            status_item = self.file_list.item(row, self.file_list.COL_STATUS)
            if status_item and status_item.data(Qt.ItemDataRole.UserRole) in (
                FileStatus.DONE,
                FileStatus.UNAVAILABLE,
            ):
                has_done = True
                break
        self.clear_btn.setEnabled(has_done)

    def _toggle_log_visibility(self):
        """Toggle the visibility of the log view."""
        is_visible = self.log_view.isVisible()
        self._set_log_visibility(not is_visible)

    def _set_log_visibility(self, visible):
        """Set the visibility of the log view and update button text."""
        self.log_view.setVisible(visible)
        if visible:
            self.log_toggle_btn.setText("Hide Log")
        else:
            self.log_toggle_btn.setText("Show Log")
        # Save to settings
        if hasattr(self.settings, 'set_log_visible'):
            self.settings.set_log_visible(visible)

    def closeEvent(self, event):
        """Handle window close."""
        # Check if any operations are in progress
        is_transferring = self.worker and self.worker.isRunning()
        is_cover_updating = (self._cover_update_worker
                             and self._cover_update_worker.isRunning())
        is_cover_scanning = (self._cover_scan_worker
                             and self._cover_scan_worker.isRunning())
        is_downloading = self._cover_download_worker and self._cover_download_worker.has_work()

        if is_transferring or is_downloading or is_cover_updating or is_cover_scanning:
            if is_transferring:
                msg = "A transfer is currently in progress."
            elif is_cover_updating or is_cover_scanning:
                msg = "A cover update is currently in progress."
            else:
                msg = "Cover downloads are in progress."
            reply = QMessageBox.question(
                self, 'Confirm Exit',
                f"{msg}\n\nDo you want to stop the operation and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

        # Stop auto-refresh timer FIRST to prevent new scans from starting
        self._scan_timer.stop()

        # Stop spinner animation
        self.scan_indicator.hide()

        # Stop port scanner if running
        if self._scanner_worker and self._scanner_worker.isRunning():
            self._scanner_worker.stop()
            self._scanner_worker.wait(1000)
            if not self._scanner_worker.isRunning():
                self._scanner_worker.deleteLater()

        # Stop cover download worker if running
        if self._cover_download_worker and self._cover_download_worker.isRunning():
            self._cover_download_worker.stop()
            self._cover_download_worker.wait(6000)
            if not self._cover_download_worker.isRunning():
                self._cover_download_worker.deleteLater()

        # Stop cover update / scan workers if running
        for worker in (self._cover_update_worker, self._cover_scan_worker):
            if worker and worker.isRunning():
                worker.stop()
                worker.wait(6000)
                if not worker.isRunning():
                    worker.deleteLater()

        # Stop transfer if running
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(6000)
            if not self.worker.isRunning():
                self.worker.deleteLater()

        # Process events to ensure threads are cleaned up before exit
        QApplication.processEvents()

        # Save settings
        self.settings.set_window_geometry(self.saveGeometry())

        event.accept()
