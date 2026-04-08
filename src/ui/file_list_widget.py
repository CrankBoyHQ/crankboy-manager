"""File list widget with drag-and-drop support using QTableWidget for proper column alignment."""

import os
from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QAbstractItemView,
    QPushButton, QHBoxLayout, QWidget, QLabel, QProgressBar, QHeaderView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from src.core.transfer_engine import get_file_info_with_crc
from src.core.constants import FileStatus


class FileListWidget(QTableWidget):
    """Table widget with drag-and-drop support for ROM files."""

    files_added = pyqtSignal(list)  # List of filepaths
    file_removed = pyqtSignal(str)  # Filepath removed
    log_message = pyqtSignal(str)  # Log messages for ZIP extraction

    def __init__(self, parent=None):
        super().__init__(parent)
        self.files_info = {}  # filepath -> file_info dict
        self.filepaths = []  # Ordered list of filepaths

        # Setup table
        self.setColumnCount(6)
        self.setHorizontalHeaderLabels(["Name", "Original Size", "Compressed", "Ratio", "Status", "Progress"])
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setAcceptDrops(True)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)

        # Set column resize modes
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Name stretches
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)    # Original Size fixed
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)    # Compressed fixed
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)    # Ratio fixed
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)    # Status fixed
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)    # Progress fixed

        # Set column widths
        self.setColumnWidth(1, 100)  # Original Size
        self.setColumnWidth(2, 100)  # Compressed
        self.setColumnWidth(3, 60)   # Ratio
        self.setColumnWidth(4, 100)  # Status
        self.setColumnWidth(5, 100)  # Progress

        # Set minimum width for the table
        self.setMinimumWidth(700)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """Accept drag events with URLs."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Accept move events during drag."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        """Handle dropped files and folders."""
        if not event.mimeData().hasUrls():
            event.ignore()
            return

        added_files = []
        for url in event.mimeData().urls():
            filepath = url.toLocalFile()
            if os.path.isdir(filepath):
                # Scan folder for ROM files
                added_files.extend(self._scan_folder(filepath))
            elif filepath.lower().endswith('.zip'):
                # Extract ROMs from ZIP file (adds directly to list)
                self._extract_zip(filepath)
            elif self._is_valid_rom(filepath):
                added_files.append(filepath)

        if added_files:
            self._add_files(added_files)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _scan_folder(self, folder_path):
        """Scan folder for ROM files and ZIP archives."""
        files = []
        try:
            for entry in os.scandir(folder_path):
                if entry.is_file():
                    if entry.path.lower().endswith('.zip'):
                        # Extract ROMs from ZIP files found in folders (adds directly to list)
                        self._extract_zip(entry.path)
                    elif self._is_valid_rom(entry.path):
                        files.append(entry.path)
                elif entry.is_dir():
                    files.extend(self._scan_folder(entry.path))
        except PermissionError:
            pass
        return files

    def _is_valid_rom(self, filepath):
        """Check if file is a valid ROM or ZIP archive."""
        ext = os.path.splitext(filepath)[1].lower()
        return ext in ['.gb', '.gbc', '.gbz', '.zip']

    def _extract_zip(self, zip_path):
        """Extract ROM files from ZIP archive and add them to the list."""
        import zipfile
        import tempfile

        extracted_files = []
        temp_dir = tempfile.mkdtemp(prefix='crankboy_zip_')

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Iterate through all files in ZIP
                for item in zf.namelist():
                    # Skip directories
                    if item.endswith('/'):
                        continue

                    # Skip macOS resource fork files (dot files)
                    if os.path.basename(item).startswith('._'):
                        continue

                    # Check if it's a ROM file
                    ext = os.path.splitext(item)[1].lower()
                    if ext in ['.gb', '.gbc', '.gbz', '.zip']:
                        # Extract to temp directory
                        zf.extract(item, temp_dir)
                        extracted_path = os.path.join(temp_dir, item)
                        extracted_files.append(extracted_path)

        except zipfile.BadZipFile:
            self.log_message.emit(f"Error: {os.path.basename(zip_path)} is not a valid ZIP file")
            return 0
        except Exception as e:
            self.log_message.emit(f"Error extracting {os.path.basename(zip_path)}: {e}")
            return 0

        # Log extraction count
        if extracted_files:
            self.log_message.emit(f"Extracted {len(extracted_files)} ROM(s) from {os.path.basename(zip_path)}")
            # Add extracted files to the list (duplicate checking handled by _add_files)
            self._add_files(extracted_files)
        else:
            self.log_message.emit(f"No ROM files found in {os.path.basename(zip_path)}")

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
                self.setItem(row, 0, name_item)

                # Original Size column (right aligned)
                orig_kb = file_info['original_size'] / 1024
                orig_size_text = f"{orig_kb:.1f} KB"
                orig_size_item = QTableWidgetItem(orig_size_text)
                orig_size_item.setFlags(orig_size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                orig_size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(row, 1, orig_size_item)

                # Compressed column (just the size, right aligned)
                if file_info['is_user_gbz']:
                    comp_text = "-"
                else:
                    gbz_kb = file_info['gbz_size'] / 1024
                    comp_text = f"{gbz_kb:.1f} KB"

                comp_item = QTableWidgetItem(comp_text)
                comp_item.setFlags(comp_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                comp_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(row, 2, comp_item)

                # Ratio column (just the percentage, centered)
                if file_info['is_user_gbz']:
                    ratio_text = "GBZ"
                else:
                    savings = (1 - file_info['gbz_size'] / file_info['original_size']) * 100
                    ratio_text = f"{savings:.0f}%"

                ratio_item = QTableWidgetItem(ratio_text)
                ratio_item.setFlags(ratio_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                ratio_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.setItem(row, 3, ratio_item)

                # Status column (centered)
                status_item = QTableWidgetItem(FileStatus.PENDING.value)
                status_item.setData(Qt.ItemDataRole.UserRole, FileStatus.PENDING)
                status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.setItem(row, 4, status_item)

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
                self.setCellWidget(row, 5, progress_widget)
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
            if filepath.lower().endswith('.zip'):
                # Extract ROMs from ZIP file (adds directly to list)
                self._extract_zip(filepath)
            elif self._is_valid_rom(filepath):
                all_files.append(filepath)
        # Add regular ROM files (ZIP files are already added by _extract_zip)
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
            status_item = self.item(row, 4)  # Status is column 4
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
            progress_widget = self.cellWidget(row, 5)  # Progress is now column 5
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
            status_item = self.item(row, 4)  # Status is now column 4
            if status_item:
                status_item.setText(status.value)
                status_item.setData(Qt.ItemDataRole.UserRole, status)
                # Qt automatically handles text color for dark/light mode
        except ValueError:
            pass

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
