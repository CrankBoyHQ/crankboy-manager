"""File list widget with drag-and-drop support using QTableWidget for proper column alignment."""

import os
from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QAbstractItemView,
    QPushButton, QHBoxLayout, QWidget, QLabel, QProgressBar, QHeaderView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtDBus import QDBusConnection, QDBusMessage
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from src.core.transfer_engine import get_file_info_with_crc
from src.core.constants import FileStatus, ArtStatus, ART_STATUS_LEGEND
from src.core.database import database as rom_database
from src.core import archive


PORTAL_FILETRANSFER_MIME = "application/vnd.portal.filetransfer"


def _retrieve_portal_files(key: str):
    """Call org.freedesktop.portal.FileTransfer.RetrieveFiles to resolve a
    drag-and-drop key into sandbox-accessible file paths. Returns a list of
    paths, or None if the call fails.

    Implemented based on https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.FileTransfer.html
    
    On platforms other than Linux or if no D-Bus API is available, we return None
    
    TODO: Replace this with the equivalent Qt library call once
    https://qt-project.atlassian.net/browse/QTBUG-91357 is resolved"""
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    msg = QDBusMessage.createMethodCall(
        "org.freedesktop.portal.Documents",
        "/org/freedesktop/portal/documents",
        "org.freedesktop.portal.FileTransfer",
        "RetrieveFiles",
    )
    msg.setArguments([key, {}])
    reply = bus.call(msg)
    if reply.type() != QDBusMessage.MessageType.ReplyMessage:
        return None
    args = reply.arguments()
    if not args:
        return None
    return list(args[0])


class FileListWidget(QTableWidget):
    """Table widget with drag-and-drop support for ROM files."""

    # Column indices (kept in one place so callers don't hard-code positions).
    COL_NAME = 0
    COL_ORIG_SIZE = 1
    COL_COMPRESSED = 2
    COL_RATIO = 3
    COL_ART = 4
    COL_STATUS = 5
    COL_PROGRESS = 6

    files_added = pyqtSignal(list)  # List of filepaths
    file_removed = pyqtSignal(str)  # Filepath removed
    log_message = pyqtSignal(str)  # Log messages for archive extraction
    delete_requested = pyqtSignal()  # Delete/Backspace pressed with a selection

    def __init__(self, parent=None):
        super().__init__(parent)
        self.files_info = {}  # filepath -> file_info dict
        self.filepaths = []  # Ordered list of filepaths

        # Setup table
        self.setColumnCount(7)
        self.setHorizontalHeaderLabels(
            ["Name", "Original Size", "Compressed", "Ratio", "Art", "Status", "Progress"]
        )
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setAcceptDrops(True)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)

        # Set column resize modes
        header = self.horizontalHeader()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_ORIG_SIZE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.COL_COMPRESSED, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.COL_RATIO, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.COL_ART, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.COL_PROGRESS, QHeaderView.ResizeMode.Fixed)

        # Set column widths
        self.setColumnWidth(self.COL_ORIG_SIZE, 100)
        self.setColumnWidth(self.COL_COMPRESSED, 100)
        self.setColumnWidth(self.COL_RATIO, 60)
        self.setColumnWidth(self.COL_ART, 80)
        self.setColumnWidth(self.COL_STATUS, 100)
        self.setColumnWidth(self.COL_PROGRESS, 100)

        # Legend tooltip on the Art column header.
        header_item = self.horizontalHeaderItem(self.COL_ART)
        if header_item is not None:
            header_item.setToolTip(ART_STATUS_LEGEND)

        # Set minimum width for the table
        self.setMinimumWidth(780)

    def keyPressEvent(self, event):
        """Emit delete_requested on Delete/Backspace when a row is selected."""
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.selectedItems():
                self.delete_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """Accept drag events with URLs."""
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasFormat(PORTAL_FILETRANSFER_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Accept move events during drag."""
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasFormat(PORTAL_FILETRANSFER_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        """Handle dropped files and folders. Prefer FileTransfer portal mime type, fall back to text/uri-list"""
        mime = event.mimeData()
        filepaths = []

        if mime.hasFormat(PORTAL_FILETRANSFER_MIME):
            key = bytes(mime.data(PORTAL_FILETRANSFER_MIME)).decode("utf-8").strip("\x00")
            portal_paths = _retrieve_portal_files(key)
            if portal_paths is not None:
                filepaths = portal_paths

        if not filepaths and mime.hasUrls():
            filepaths = [url.toLocalFile() for url in mime.urls() if url.toLocalFile()]

        if not filepaths:
            event.ignore()
            return

        added_files = []
        for filepath in filepaths:
            if os.path.isdir(filepath):
                # Scan folder for ROM files
                added_files.extend(self._scan_folder(filepath))
            elif archive.is_archive(filepath):
                # Extract ROMs from the archive (adds directly to list)
                self._extract_archive(filepath)
            elif self._is_valid_rom(filepath):
                added_files.append(filepath)

        if added_files:
            self._add_files(added_files)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _scan_folder(self, folder_path):
        """Scan folder for ROM files and archives."""
        files = []
        try:
            for entry in os.scandir(folder_path):
                if entry.is_file():
                    if archive.is_archive(entry.path):
                        # Extract ROMs from archives found in folders (adds directly to list)
                        self._extract_archive(entry.path)
                    elif self._is_valid_rom(entry.path):
                        files.append(entry.path)
                elif entry.is_dir():
                    files.extend(self._scan_folder(entry.path))
        except PermissionError:
            pass
        return files

    def _is_valid_rom(self, filepath):
        """Check if file is a valid ROM."""
        ext = os.path.splitext(filepath)[1].lower()
        return ext in archive.ROM_EXTS

    def _extract_archive(self, archive_path):
        """Extract every ROM from an archive and add them to the list.

        The normal ROM-upload flow accepts any number of ROMs, so all of
        them are added.
        """
        name = os.path.basename(archive_path)
        try:
            extracted_files = archive.extract_roms(archive_path)
        except archive.ArchiveError as e:
            self.log_message.emit(f"Error: {e}")
            return 0

        if extracted_files:
            self.log_message.emit(f"Extracted {len(extracted_files)} ROM(s) from {name}")
            # Add extracted files to the list (duplicate checking handled by _add_files)
            self._add_files(extracted_files)
        else:
            self.log_message.emit(f"No ROM files found in {name}")

        return len(extracted_files)

    def _add_files(self, filepaths):
        """Add files to the list."""
        added = []
        # Get set of existing filenames to prevent duplicates
        existing_names = {os.path.basename(fp) for fp in self.filepaths}

        for filepath in filepaths:
            filename = os.path.basename(filepath)
            if filepath in self.files_info or filename in existing_names:
                self.log_message.emit(f"Skipping {filename} (already in list)")
                continue  # Skip duplicates (by full path or filename)

            try:
                file_info = get_file_info_with_crc(filepath)
                self.files_info[filepath] = file_info
                self.filepaths.append(filepath)

                row = self.rowCount()
                self.insertRow(row)

                # Name column
                name_item = QTableWidgetItem(file_info['filename'])
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(row, self.COL_NAME, name_item)

                # Original Size column (right aligned)
                orig_kb = file_info['original_size'] / 1024
                orig_size_text = f"{orig_kb:.1f} KB"
                orig_size_item = QTableWidgetItem(orig_size_text)
                orig_size_item.setFlags(orig_size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                orig_size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(row, self.COL_ORIG_SIZE, orig_size_item)

                # Compressed column (just the size, right aligned)
                if file_info['is_user_gbz']:
                    comp_text = "-"
                else:
                    gbz_kb = file_info['gbz_size'] / 1024
                    comp_text = f"{gbz_kb:.1f} KB"

                comp_item = QTableWidgetItem(comp_text)
                comp_item.setFlags(comp_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                comp_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(row, self.COL_COMPRESSED, comp_item)

                # Ratio column (just the percentage, centered)
                if file_info['is_user_gbz']:
                    ratio_text = "GBZ"
                else:
                    savings = (1 - file_info['gbz_size'] / file_info['original_size']) * 100
                    ratio_text = f"{savings:.0f}%"

                ratio_item = QTableWidgetItem(ratio_text)
                ratio_item.setFlags(ratio_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                ratio_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.setItem(row, self.COL_RATIO, ratio_item)

                # Art column (centered). CRC32 is already computed synchronously
                # by get_file_info_with_crc, so we can resolve Match/No Match
                # immediately.
                crc = file_info.get('original_crc')
                if crc and rom_database.get_cover_filename(crc):
                    initial_art = ArtStatus.MATCH
                elif crc:
                    initial_art = ArtStatus.NO_MATCH
                else:
                    initial_art = ArtStatus.UNKNOWN
                art_item = QTableWidgetItem(initial_art.value)
                art_item.setData(Qt.ItemDataRole.UserRole, initial_art)
                art_item.setFlags(art_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                art_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                art_item.setToolTip(ART_STATUS_LEGEND)
                self.setItem(row, self.COL_ART, art_item)

                # Status column (centered)
                status_item = QTableWidgetItem(FileStatus.PENDING.value)
                status_item.setData(Qt.ItemDataRole.UserRole, FileStatus.PENDING)
                status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.setItem(row, self.COL_STATUS, status_item)

                # Progress column - embed progress bar with % text
                progress_widget = QWidget()
                progress_layout = QHBoxLayout(progress_widget)
                progress_layout.setContentsMargins(5, 2, 5, 2)
                progress_bar = QProgressBar()
                progress_bar.setRange(0, 100)
                progress_bar.setValue(0)
                progress_bar.setTextVisible(True)
                progress_bar.setMaximumHeight(20)
                progress_bar.setFormat("%p%")  # Show percentage
                progress_bar.setStyleSheet("""
                    QProgressBar {
                        border: 1px solid palette(mid);
                        border-radius: 3px;
                        background: palette(base);
                        text-align: center;
                    }
                    QProgressBar::chunk {
                        background: palette(highlight);
                    }
                """)
                progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
                progress_layout.addWidget(progress_bar)
                self.setCellWidget(row, self.COL_PROGRESS, progress_widget)
                # Store reference to progress bar
                progress_widget.progress_bar = progress_bar

                added.append(filepath)
                existing_names.add(filename)  # Track to prevent duplicates within same batch
            except Exception as e:
                print(f"Error adding file {filepath}: {e}")

        if added:
            self.files_added.emit(added)

    def add_files(self, filepaths):
        """Public method to add files (e.g., from file dialog)."""
        all_files = []
        for filepath in filepaths:
            if archive.is_archive(filepath):
                # Extract ROMs from the archive (adds directly to list)
                self._extract_archive(filepath)
            elif self._is_valid_rom(filepath):
                all_files.append(filepath)
        # Add regular ROM files (archives are already added by _extract_archive)
        if all_files:
            self._add_files(all_files)

    def remove_file(self, filepath):
        """Remove a file from the list."""
        if filepath not in self.files_info:
            return

        # Find row index
        try:
            row = self.filepaths.index(filepath)
            self.removeRow(row)
            self.filepaths.pop(row)
        except ValueError:
            pass

        del self.files_info[filepath]
        self.file_removed.emit(filepath)

    def clear_completed(self):
        """Remove completed files from the list."""
        to_remove = []
        for row, filepath in enumerate(self.filepaths):
            status_item = self.item(row, self.COL_STATUS)
            if status_item:
                status = status_item.data(Qt.ItemDataRole.UserRole)
                if status == FileStatus.DONE:
                    to_remove.append(filepath)

        for filepath in to_remove:
            self.remove_file(filepath)

    def get_files(self):
        """Get list of file info dicts in order."""
        return [self.files_info[fp] for fp in self.filepaths]

    def set_file_progress(self, filepath, bytes_sent, total_bytes, is_error=False):
        """Update progress for a file."""
        if filepath not in self.files_info:
            return

        try:
            row = self.filepaths.index(filepath)
            progress_widget = self.cellWidget(row, self.COL_PROGRESS)
            if progress_widget and hasattr(progress_widget, 'progress_bar'):
                progress = int((bytes_sent / total_bytes) * 100)
                progress_bar = progress_widget.progress_bar
                progress_bar.setValue(progress)

                # Update color based on progress and error state
                if is_error:
                    progress_bar.setStyleSheet("""
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
                elif progress >= 100:
                    progress_bar.setStyleSheet("""
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
                else:
                    progress_bar.setStyleSheet("""
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
        except ValueError:
            pass

    def set_file_status(self, filepath, status: FileStatus):
        """Update status for a file."""
        if filepath not in self.files_info:
            return

        try:
            row = self.filepaths.index(filepath)
            status_item = self.item(row, self.COL_STATUS)
            if status_item:
                status_item.setText(status.value)
                status_item.setData(Qt.ItemDataRole.UserRole, status)
                # Qt automatically handles text color for dark/light mode
        except ValueError:
            pass

    def set_art_status(self, filepath, status: ArtStatus):
        """Update cover-art status for a file."""
        if filepath not in self.files_info:
            return

        try:
            row = self.filepaths.index(filepath)
            art_item = self.item(row, self.COL_ART)
            if art_item:
                art_item.setText(status.value)
                art_item.setData(Qt.ItemDataRole.UserRole, status)
        except ValueError:
            pass

    def get_art_status(self, filepath):
        """Return the current ArtStatus for a file, or None if unknown."""
        if filepath not in self.files_info:
            return None
        try:
            row = self.filepaths.index(filepath)
            art_item = self.item(row, self.COL_ART)
            if art_item:
                return art_item.data(Qt.ItemDataRole.UserRole)
        except ValueError:
            pass
        return None

    def mark_transferring(self, filepath):
        """Mark a file as currently transferring."""
        self.set_file_status(filepath, FileStatus.TRANSFERRING)

    def clear(self):
        """Clear all files."""
        super().clear()
        self.files_info.clear()
        self.filepaths.clear()
        # Re-add header row
        self.setRowCount(0)
