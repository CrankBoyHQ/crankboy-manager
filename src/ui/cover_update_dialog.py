"""Dialog listing the device's installed games for selective cover updates."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.cover_update_worker import CoverUpdateWorker
from src.core.database import database as rom_database


class CoverUpdateDialog(QDialog):
    """Lists the installed games on the device and lets the user pick
    which covers to replace. Defaults to filling gaps only: games without
    a device cover are pre-checked, existing ones are not."""

    start_requested = pyqtSignal(list)  # selected entries: dicts with crc32 + rom_name
    scan_finished = pyqtSignal()  # scan worker exited; serial port free again

    COL_REPLACE = 0
    COL_GAME = 1
    COL_DEVICE_COVER = 2
    COL_DB = 3

    def __init__(self, parent, port, options, log_callback=None):
        super().__init__(parent)
        self._port = port
        self._options = options
        self._log = log_callback or (lambda msg: None)
        self._scan_worker = None

        self.setWindowTitle("Update covers")
        self.resize(560, 420)

        layout = QVBoxLayout(self)
        self._status_label = QLabel("Reading installed games from device...")
        layout.addWidget(self._status_label)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["", "Game", "On device", "In database"])
        self._table.verticalHeader().setVisible(False)
        self._table.setColumnWidth(self.COL_REPLACE, 26)
        self._table.horizontalHeader().setSectionResizeMode(
            self.COL_GAME, QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        presets = QHBoxLayout()
        for text, handler in (
            ("Select all", self._select_all),
            ("Missing only", self._select_missing),
            ("None", self._select_none),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            presets.addWidget(btn)
        presets.addStretch()
        layout.addLayout(presets)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._start_btn = QPushButton("Update selected")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self._start_btn)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        self.start_scan()

    def start_scan(self):
        """Start the device scan."""
        self._scan_worker = CoverUpdateWorker(
            self._port, self._options, list_only=True
        )
        self._scan_worker.games_ready.connect(self._on_games_ready)
        self._scan_worker.error_occurred.connect(self._on_scan_error)
        self._scan_worker.all_completed.connect(self._on_scan_finished)
        self._scan_worker.start()

    def done(self, result):
        # stop an in-flight scan so the port frees up for the next action
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.stop()
        super().done(result)

    def scan_worker(self):
        return self._scan_worker

    def _on_scan_finished(self, _ok=False):
        self.scan_finished.emit()

    # ---- scan results -----------------------------------------------------

    def _on_games_ready(self, games):
        self._status_label.setText(
            f"{len(games)} installed game(s). Select which covers to replace."
        )
        self._table.setRowCount(len(games))
        selected_crcs = set()

        for row, record in enumerate(games):
            crc = record.get("crc32")
            db_name = rom_database.get_cover_filename(crc) if crc else None

            game_item = QTableWidgetItem(record.get("display_name") or record.get("path") or "?")
            game_item.setData(Qt.ItemDataRole.UserRole, record)
            self._table.setItem(row, self.COL_GAME, game_item)

            cover_state = record.get("cover") or "-"
            device_item = QTableWidgetItem("none" if cover_state == "-" else "yes")
            device_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, self.COL_DEVICE_COVER, device_item)

            db_item = QTableWidgetItem("yes" if db_name else "no")
            db_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, self.COL_DB, db_item)

            replace_box = QCheckBox()
            replace_box.setEnabled(db_name is not None)
            replace_box.toggled.connect(self._update_start_enabled)
            if db_name:
                # default: fill gaps, keep existing covers unless picked
                if cover_state == "-":
                    replace_box.setChecked(True)
                    selected_crcs.add(crc)
            check_wrap = QWidget()
            check_layout = QHBoxLayout(check_wrap)
            check_layout.setContentsMargins(0, 0, 0, 0)
            check_layout.addWidget(
                replace_box, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setCellWidget(row, self.COL_REPLACE, check_wrap)

        self._start_btn.setEnabled(len(selected_crcs) > 0)
        self._update_start_enabled()

    def _on_scan_error(self, _filename, error):
        self._status_label.setText(f"Scan failed: {error}")

    # ---- presets -----------------------------------------------------------

    def _selected_crcs(self):
        crcs = set()
        for row in range(self._table.rowCount()):
            box = self._table.cellWidget(row, self.COL_REPLACE)
            if isinstance(box, QWidget):
                box = box.findChild(QCheckBox)
            if box and box.isEnabled() and box.isChecked():
                record = self._row_record(row)
                if record and record.get("crc32"):
                    crcs.add(record["crc32"])
        return crcs

    def _row_record(self, row):
        game_item = self._table.item(row, self.COL_GAME)
        return game_item.data(Qt.ItemDataRole.UserRole) if game_item else None

    def _update_start_enabled(self):
        self._start_btn.setEnabled(len(self._selected_crcs()) > 0)

    def _select_all(self):
        for row in range(self._table.rowCount()):
            box = self._table.cellWidget(row, self.COL_REPLACE)
            if isinstance(box, QWidget):
                box = box.findChild(QCheckBox)
            if box and box.isEnabled():
                box.setChecked(True)
        self._update_start_enabled()

    def _select_missing(self):
        for row in range(self._table.rowCount()):
            box = self._table.cellWidget(row, self.COL_REPLACE)
            if isinstance(box, QWidget):
                box = box.findChild(QCheckBox)
            if box and box.isEnabled():
                record = self._row_record(row)
                device_item = self._table.item(row, self.COL_DEVICE_COVER)
                missing = device_item is not None and device_item.text() == "none"
                box.setChecked(missing)
        self._update_start_enabled()

    def _select_none(self):
        for row in range(self._table.rowCount()):
            box = self._table.cellWidget(row, self.COL_REPLACE)
            if isinstance(box, QWidget):
                box = box.findChild(QCheckBox)
            if box:
                box.setChecked(False)
        self._update_start_enabled()

    def _on_start(self):
        # scan thread may still be closing the port right after listing
        if self._scan_worker and self._scan_worker.isRunning():
            return
        entries = []
        for row in range(self._table.rowCount()):
            box = self._table.cellWidget(row, self.COL_REPLACE)
            if isinstance(box, QWidget):
                box = box.findChild(QCheckBox)
            if box and box.isEnabled() and box.isChecked():
                record = self._row_record(row)
                if record and record.get("crc32"):
                    entries.append(
                        {
                            "crc32": record["crc32"],
                            "rom_name": record.get("path", "?").rsplit("/", 1)[-1],
                        }
                    )
        if not entries:
            return
        self._log(f"Updating covers for {len(entries)} game(s)...")
        self.start_requested.emit(entries)
        self.accept()
