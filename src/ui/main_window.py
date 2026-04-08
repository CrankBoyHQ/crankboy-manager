"""Main window for CrankBoy Manager."""

import os
import sys
import time
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QCheckBox,
    QFileDialog, QProgressBar, QTextEdit, QMessageBox,
    QFrame, QGroupBox, QApplication
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon

from src.ui.file_list_widget import FileListWidget
from src.core.serial_worker import SerialWorker
from src.core.port_scanner import scan_for_crankboy
from src.core.port_scanner_worker import PortScannerWorker
from src.core.cover_download_worker import CoverDownloadWorker
from src.core.transfer_engine import send_command, read_response
from src.core.constants import FileStatus
from src.ui.spinner import Spinner


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.worker = None
        self._cover_download_worker = None
        self._transfer_stopped = False  # Track if transfer was stopped by user
        self._crankboy_connected = False  # Track if CrankBoy is currently connected

        self.setWindowTitle("CrankBoy Manager")
        self.setMinimumSize(800, 700)

        # Set window icon
        self._set_window_icon()

        # Restore window geometry
        geometry = settings.get_window_geometry()
        if geometry:
            self.restoreGeometry(geometry)

        self._setup_ui()
        self._connect_signals()

        # Setup port scanner worker
        self._scanner_worker = PortScannerWorker()
        self._scanner_worker.scan_complete.connect(self._on_scan_complete)

        # Setup auto-refresh timer (3 seconds)
        self._scan_timer = QTimer()
        self._scan_timer.timeout.connect(self._start_port_scan)
        self._scan_timer.start(3000)

        # Initial scan
        self.port_combo.addItem("Scanning for Playdate...", None)
        self.port_combo.setEnabled(False)
        self.scan_indicator.show()
        self._start_port_scan()

        # Update button states
        self._update_transfer_button_state()
        self._update_clear_button_state()

    def _setup_ui(self):
        """Setup the user interface."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # === File List (with drag & drop) ===
        list_header = QHBoxLayout()
        list_header.addWidget(QLabel("Transfer Queue"))
        list_header.addStretch()

        self.add_btn = QPushButton("Add Files…")
        list_header.addWidget(self.add_btn)

        self.remove_btn = QPushButton("Remove Selected")
        self.remove_btn.setEnabled(False)  # Disabled until items are selected
        list_header.addWidget(self.remove_btn)

        layout.addLayout(list_header)

        self.file_list = FileListWidget()
        layout.addWidget(self.file_list, stretch=1)

        # === Action Buttons ===
        btn_layout = QHBoxLayout()

        self.transfer_btn = QPushButton("Start Transfer")
        self.transfer_btn.setMinimumWidth(150)  # Accommodate "Stop Transfer" text
        btn_layout.addWidget(self.transfer_btn)

        btn_layout.addStretch()

        self.clear_btn = QPushButton("Clear Completed")
        btn_layout.addWidget(self.clear_btn)

        layout.addLayout(btn_layout)

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
        self.keep_compressed_cb = QCheckBox("GBZ")
        self.keep_compressed_cb.setChecked(self.settings.get_keep_compressed())
        self.keep_compressed_cb.setToolTip("Store ROMs in GBZ format on device")
        controls_layout.addWidget(self.keep_compressed_cb)


        self.restart_cb = QCheckBox("Restart")
        self.restart_cb.setChecked(self.settings.get_auto_restart())
        self.restart_cb.setToolTip("Restart CrankBoy after all transfers are completed")
        controls_layout.addWidget(self.restart_cb)

        layout.addWidget(controls)

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
        progress_layout.addWidget(self.overall_progress, stretch=1)
        self.progress_label = QLabel("0/0")
        progress_layout.addWidget(self.progress_label)
        layout.addLayout(progress_layout)

    def _connect_signals(self):
        """Connect UI signals."""
        self.add_btn.clicked.connect(self._add_files_dialog)
        self.remove_btn.clicked.connect(self._remove_selected_files)
        self.transfer_btn.clicked.connect(self._on_transfer_button_clicked)
        self.clear_btn.clicked.connect(self._clear_completed)

        self.file_list.files_added.connect(self._on_files_added)
        self.file_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.file_list.log_message.connect(self._log)

    def _set_window_icon(self):
        """Set the window icon from the bundled icon file."""
        from pathlib import Path
        from PyQt6.QtGui import QIcon

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
        # Skip if transfer is in progress or scan already running
        if self.worker and self.worker.isRunning():
            return
        if self._scanner_worker and self._scanner_worker.isRunning():
            return

        # Show scanning indicator if no CrankBoy is currently connected
        if not self._crankboy_connected:
            self.scan_indicator.show()

        # Start scan in background
        self._scanner_worker.start()

    def _on_scan_complete(self, result):
        """Handle scan completion from worker thread."""
        # Check if state changed
        was_connected = self._crankboy_connected
        is_connected = result['status'] == 'connected_running'

        # Only hide if we actually found something
        if is_connected:
            self.scan_indicator.hide()

        # Save current selection to restore it if possible
        current_selection = self.port_combo.currentData()

        # Handle new connection status
        if is_connected:
            self._crankboy_connected = True
            self.port_combo.setEnabled(True)

            # Update list of ports if it changed
            new_ports = result['ports']

            # Simple check to see if ports list is different
            current_ports = []
            for i in range(self.port_combo.count()):
                data = self.port_combo.itemData(i)
                if data:
                    current_ports.append(data)

            new_port_devices = [p['device'] for p in new_ports]

            if set(current_ports) != set(new_port_devices):
                self.port_combo.clear()
                for i, port in enumerate(new_ports):
                    device = port['device']
                    version = port['version']
                    label = f"CrankBoy {version}"
                    self.port_combo.addItem(label, device)
                    self.port_combo.setItemData(i, f"Device: {device}", Qt.ItemDataRole.ToolTipRole)

                # Restore selection if it still exists
                index = self.port_combo.findData(current_selection)
                if index >= 0:
                    self.port_combo.setCurrentIndex(index)
                else:
                    self.port_combo.setCurrentIndex(0)

            # Update main tooltip for current selection
            active_device = self.port_combo.currentData()
            if active_device:
                self.port_combo.setToolTip(f"Connected to {active_device}")

            if not was_connected:
                self._log(result['message'])

        else:
            # Not connected or not running
            self._crankboy_connected = False
            self.port_combo.setEnabled(False)
            self.port_combo.setToolTip("")

            current_text = self.port_combo.currentText()
            expected_text = "CrankBoy not running" if result['status'] == 'connected_not_running' else "Playdate not connected"

            if current_text != expected_text:
                self.port_combo.clear()
                self.port_combo.addItem(expected_text, None)
                if not was_connected or was_connected: # Always log if status message changed
                     self._log(result['message'])

        self._update_transfer_button_state()

    def _add_files_dialog(self):
        """Open file dialog to add files."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select ROM Files",
            "",
            "Game Boy ROMs (*.gb *.gbc *.gbz);;ZIP Archives (*.zip);;All Files (*.*)"
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

        # Stop any existing cover download worker
        if self._cover_download_worker and self._cover_download_worker.isRunning():
            self._cover_download_worker.stop()
            self._cover_download_worker.wait(1000)
            self._cover_download_worker.deleteLater()

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

        # Start cover download worker with references to existing file_info objects
        self._cover_download_worker = CoverDownloadWorker(file_info_list, max_concurrent=20)
        self._cover_download_worker.cover_started.connect(self._on_cover_download_started)
        self._cover_download_worker.cover_completed.connect(self._on_cover_download_completed)
        self._cover_download_worker.all_completed.connect(self._on_all_covers_completed)

        self._log("  Starting cover downloads...")
        self._cover_download_worker.start()

    def _on_cover_download_started(self, rom_filename, cover_filename):
        """Handle cover download starting."""
        self._log(f"    Downloading cover for {rom_filename}...")

    def _on_cover_download_completed(self, rom_filename, success, message):
        """Handle cover download completion."""
        if success:
            self._log(f"    ✓ Cover ready for {rom_filename}")
        else:
            self._log(f"    Cover not available for {rom_filename}: {message}")

    def _on_all_covers_completed(self):
        """Handle all cover downloads completing."""
        self._log("  All cover downloads completed")
        if self._cover_download_worker:
            self._cover_download_worker.deleteLater()
        self._cover_download_worker = None

    def _on_selection_changed(self):
        """Handle selection change in file list."""
        # Only enable remove button if not currently transferring
        is_transferring = self.worker is not None and self.worker.isRunning()
        has_selection = len(self.file_list.selectedItems()) > 0
        self.remove_btn.setEnabled(has_selection and not is_transferring)

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
            status_item = self.file_list.item(row, 4)  # Status column
            if status_item:
                status = status_item.data(Qt.ItemDataRole.UserRole)
                # Only include files that are not done
                if status != FileStatus.DONE:
                    # Get existing file_info (which has cover data from background download)
                    file_info = self.file_list.files_info.get(filepath)
                    if file_info:
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

        # Track overall progress (only for files being transferred)
        self._files_to_transfer = files
        self._current_file_index = 0
        # Include cover data size in total if present
        self._total_bytes_all_files = sum(f['gbz_size'] + (len(f['cover_data']) if f.get('cover_data') else 0) for f in files)
        self._bytes_completed = 0
        self._current_file_bytes = 0
        self._current_file_total = 0

        # Count skipped files (those with "Done" status)
        done_count = 0
        for row in range(self.file_list.rowCount()):
            status_item = self.file_list.item(row, 4)
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
            'use_sft': True  # We enabled SFT overlay before starting
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
        # Change button to "Stop Transfer"
        self.transfer_btn.setText("Stop Transfer")
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

    def _stop_transfer(self):
        """Stop the current transfer."""
        if self.worker:
            self._log("Stopping transfer...")
            self._transfer_stopped = True
            self.worker.stop()
            self.worker.wait(2000)
            self.worker.deleteLater()
            # Reset button text will be done in _on_all_completed

    def _on_transfer_button_clicked(self):
        """Handle transfer button click - start or stop based on current state."""
        if self.worker and self.worker.isRunning():
            # Transfer is running, stop it
            self._stop_transfer()
        else:
            # No transfer running, start one
            self._start_transfer()

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

        # Update overall progress
        self._current_file_bytes = bytes_sent
        total_sent = self._bytes_completed + self._current_file_bytes
        overall_progress = int((total_sent / self._total_bytes_all_files) * 100) if self._total_bytes_all_files > 0 else 0
        self.overall_progress.setValue(overall_progress)

    def _on_file_completed(self, filename, success, message):
        """Handle file transfer completion."""
        filepath = self._find_filepath_by_name(filename)
        if filepath:
            self.file_list.set_file_status(filepath, FileStatus.DONE if success else FileStatus.FAILED)
            # Mark file progress - green for success, red for failure
            self.file_list.set_file_progress(filepath, 100, 100, is_error=not success)
            self._update_clear_button_state()

        # Add to completed bytes for overall progress
        self._bytes_completed += self._current_file_total
        self._current_file_bytes = 0

        status = "✓" if success else "✗"
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

    def _on_all_completed(self, all_successful):
        """Handle all transfers completing."""
        self._set_controls_enabled(True)

        # Check for failed transfers
        failed_count = 0
        for row in range(self.file_list.rowCount()):
            status_item = self.file_list.item(row, 4)
            if status_item and status_item.data(Qt.ItemDataRole.UserRole) == FileStatus.FAILED:
                failed_count += 1

        # Set button text based on state
        if failed_count > 0:
            self.transfer_btn.setText("Retry Transfer")
        else:
            self.transfer_btn.setText("Start Transfer")

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

        # Check if there are still pending files to transfer
        pending_count = 0
        for row in range(len(self.file_list.filepaths)):
            status_item = self.file_list.item(row, 4)
            if status_item:
                status = status_item.data(Qt.ItemDataRole.UserRole)
                if status not in [FileStatus.DONE, FileStatus.FAILED]:
                    pending_count += 1

        # Update button text based on whether there are pending files
        if pending_count > 0:
            self.transfer_btn.setText("Resume Transfer")
        else:
            self.transfer_btn.setText("Start Transfer")
            self._transfer_stopped = False

        # Use deleteLater to safely cleanup the thread after it finishes
        if self.worker:
            self.worker.deleteLater()
        self.worker = None

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
        """Find filepath in file list by filename."""
        for filepath, info in self.file_list.files_info.items():
            if info['filename'] == filename:
                return filepath
        return None

    def _get_current_transferring_file(self):
        """Get the filepath of the file currently being transferred."""
        for row, filepath in enumerate(self.file_list.filepaths):
            status_item = self.file_list.item(row, 4)  # Status is column 4
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
            status_item = self.file_list.item(row, 4)
            if status_item:
                status = status_item.data(Qt.ItemDataRole.UserRole)
                if status != FileStatus.DONE:
                    return True
        return False

    def _update_transfer_button_state(self):
        """Update the enabled state of the transfer button."""
        # If a transfer is already running, the button should be enabled (it's the "Stop" button)
        if self.worker and self.worker.isRunning():
            self.transfer_btn.setEnabled(True)
            return

        has_files = self._has_transferable_files()
        self.transfer_btn.setEnabled(self._crankboy_connected and has_files)

    def _update_clear_button_state(self):
        """Update the enabled state of the clear button."""
        # If a transfer is running, it stays disabled
        if self.worker and self.worker.isRunning():
            self.clear_btn.setEnabled(False)
            return

        has_done = False
        for row in range(self.file_list.rowCount()):
            status_item = self.file_list.item(row, 4)
            if status_item and status_item.data(Qt.ItemDataRole.UserRole) == FileStatus.DONE:
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
        is_downloading = self._cover_download_worker and self._cover_download_worker.isRunning()

        if is_transferring or is_downloading:
            msg = "A transfer is currently in progress." if is_transferring else "Cover downloads are in progress."
            reply = QMessageBox.question(
                self, 'Confirm Exit',
                f"{msg}\n\nDo you want to stop the operation and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

        # Stop auto-refresh timer
        self._scan_timer.stop()

        # Stop port scanner if running
        if self._scanner_worker and self._scanner_worker.isRunning():
            self._scanner_worker.stop()
            self._scanner_worker.wait(1000)
            self._scanner_worker.deleteLater()

        # Stop cover download worker if running
        if self._cover_download_worker and self._cover_download_worker.isRunning():
            self._cover_download_worker.stop()
            self._cover_download_worker.wait(2000)
            self._cover_download_worker.deleteLater()

        # Stop transfer if running
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)
            self.worker.deleteLater()

        # Process events to ensure threads are cleaned up before exit
        QApplication.processEvents()

        # Save settings
        self.settings.set_window_geometry(self.saveGeometry())

        event.accept()
