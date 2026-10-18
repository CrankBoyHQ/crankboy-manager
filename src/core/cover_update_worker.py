"""Worker that refreshes every installed game's cover from the database.

Reads the installed games from the device (cb:games), downloads the
container format the device expects and transfers cover data only.
"""

import os
import time
import urllib.parse

import serial as pyserial

from src.core.serial_worker import SerialWorker
from src.core.transfer_engine import send_command, read_response
from src.core.database import database as rom_database
from src.core.cover_downloader import download_cover

from PyQt6.QtCore import pyqtSignal


class CoverUpdateWorker(SerialWorker):
    """Reads the installed games on the device, downloads available cover
    art and transfers only covers (no ROM payloads)."""

    games_listed = pyqtSignal(int)  # number of installed games found
    games_ready = pyqtSignal(list)  # parsed game records (list_only mode)
    cover_overall = pyqtSignal(int)  # overall update progress in percent

    def __init__(self, port, options=None, list_only=False, selected_crcs=None):
        """list_only: only enumerate installed games (dialog listing).
        selected_crcs: None = every game, else only these crc32 ints."""
        super().__init__(port, files_info=None, options=options)
        self._list_only = list_only
        self._selected_crcs = selected_crcs

    # ---- per-step helpers -------------------------------------------------

    def _sft_enable(self, ser):
        """Enter the device's Serial File Transfer mode (same lifecycle the
        ROM transfer flow drives)."""
        send_command(ser, "cb:sft:on")
        response = read_response(ser, timeout=2.0)
        if response in ("cb:sft:ok", "cb:sft:error:already-active"):
            self._sft_active = True
            return True
        self.log_message.emit(f"Failed to enable File Transfer Mode: {response}")
        return False

    def _sft_disable(self, ser):
        if not getattr(self, "_sft_active", False):
            return
        self._sft_active = False
        send_command(ser, "cb:sft:off")
        read_response(ser, timeout=1.0)

    def _read_games_header(self, ser):
        """Consume lines until the cb:games:<count> header arrives."""
        deadline = time.time() + 10.0
        while time.time() < deadline and self._is_running:
            line = read_response(ser, timeout=2.0)
            if not line:
                continue
            parts = line.split(":")
            if len(parts) == 3 and parts[0] == "cb" and parts[1] == "games" and parts[2].isdigit():
                return int(parts[2])
        return None

    def _query_installed_games(self, ser):
        """Stream cb:games records. Returns a list of dicts with
        'crc32' (int), 'path' (fullpath str) and 'display_name'; None on
        protocol failure. Values arrive urlencoded, so colons inside them
        are split-safe; decode each field after splitting.
        """
        send_command(ser, "cb:games")
        count = self._read_games_header(ser)
        if count is None:
            return None
        self.games_listed.emit(count)

        games, pending, done = [], None, 0
        deadline = time.time() + 30.0
        while done < count and time.time() < deadline and self._is_running:
            line = read_response(ser, timeout=5.0)
            if not line:
                break
            parts = line.split(":")
            if len(parts) < 4 or parts[0] != "cb" or parts[1] != "games":
                continue
            try:
                index = int(parts[2])
                kind = parts[3]
                value = parts[4] if len(parts) > 4 else ""
            except ValueError:
                continue

            if index != done:
                # out-of-order stream; flush the current record early
                if pending is not None:
                    games.append(pending)
                    done += 1
                pending = None

            if kind == "end":
                if pending is not None:
                    games.append(pending)
                    done += 1
                pending = None
                continue

            if pending is None or pending.get("_index") != index:
                pending = {"_index": index}

            if kind == "crc32":
                try:
                    pending[kind] = int(value, 16)
                except ValueError:
                    pass
            else:
                pending[kind] = urllib.parse.unquote(value)
        return games

    def _cover_basename(self, game_record):
        """Device cover files are named after the installed ROM's basename
        (the same convention the Add-to-Device flow uses)."""
        path = game_record.get("path") or ""
        rom_filename = path.rsplit("/", 1)[-1]
        return os.path.splitext(rom_filename)[0]

    def _rom_display_name(self, game_record):
        """Device ROM filename with extension (queue-row identity)."""
        path = game_record.get("path") or ""
        return path.rsplit("/", 1)[-1]

    def run(self):
        try:
            self._log(f"Connecting to {self.port}...")
            ser = pyserial.Serial(self.port, 115200, timeout=5)
            self._current_serial = ser
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            if not self._sft_enable(ser):
                self.error_occurred.emit("", "Could not enable device transfer mode")
                ser.close()
                self.all_completed.emit(False)
                return

            games = self._query_installed_games(ser)
            if games is None:
                self._sft_disable(ser)
                self.error_occurred.emit("", "Could not list installed games")
                ser.close()
                self.all_completed.emit(False)
                return
            if self._list_only:
                self.games_ready.emit(games)
                self._sft_disable(ser)
                ser.close()
                self.all_completed.emit(True)
                return

            selected_total = len(self._selected_crcs) if self._selected_crcs is not None else len(games)
            done_count = 0
            for game_record in games:
                if not self._is_running:
                    break
                crc = game_record.get("crc32")
                if crc and self._selected_crcs is not None and crc not in self._selected_crcs:
                    continue
                rom_name = self._rom_display_name(game_record)
                crc_info = rom_database.get_cover_info(crc, cvi=self.use_cvi) if crc else None
                if not crc_info:
                    self.file_completed.emit(rom_name, False, "No cover in database")
                    done_count += 1
                    self.cover_overall.emit(done_count * 100 // max(1, selected_total))
                    continue

                basename = self._cover_basename(game_record)
                if not basename:
                    done_count += 1
                    self.cover_overall.emit(done_count * 100 // max(1, selected_total))
                    continue
                ext = os.path.splitext(crc_info['filename'])[1]
                cover_filename = f"{basename}{ext}"

                self.log_message.emit(f"Updating cover: {cover_filename}")
                try:
                    cover_data = download_cover(crc, cvi=self.use_cvi)
                except Exception as e:
                    cover_data = None
                    self.log_message.emit(f"  Download failed: {e}")
                if not cover_data:
                    self.file_completed.emit(rom_name, False, "No cover file available")
                    done_count += 1
                    self.cover_overall.emit(done_count * 100 // max(1, selected_total))
                    continue

                # queue-row machinery keys on the device ROM name
                self.file_started.emit(rom_name, len(cover_data))
                ok, message = self._transfer_cover(
                    ser, cover_data, cover_filename, len(cover_data), 0
                )
                done_count += 1
                self.cover_overall.emit(done_count * 100 // max(1, selected_total))
                if not ok and not self._is_running:
                    self.file_completed.emit(rom_name, False, "User stopped")
                elif ok:
                    self.file_completed.emit(rom_name, True, message)
                    self.log_message.emit(f"  Cover updated: {cover_filename}")
                else:
                    self.file_completed.emit(rom_name, False, message)
                    self.log_message.emit(f"  Cover transfer failed: {cover_filename} - {message}")

            if not self._is_running:
                # aborted: leave the device clean, skip the refresh
                self._sft_disable(ser)
                ser.close()
                self.all_completed.emit(False)
                return
            # tell the app about the fresh files (library scene reloads live)
            send_command(ser, "cb:covers:refresh")
            response = read_response(ser, timeout=5.0)
            if response and response.startswith("cb:covers:refresh:ok"):
                self.log_message.emit("Covers refreshed on device")
            elif response and response.startswith("cb:covers:refresh:error"):
                self.log_message.emit(f"Device said: {response} (library open required)")
            else:
                self.log_message.emit("No covers:refresh response from device")

            self._sft_disable(ser)
            ser.close()
            self.all_completed.emit(True)
        except pyserial.SerialException as e:
            self.error_occurred.emit("", f"Serial error: {e}")
            self.all_completed.emit(False)
        except Exception as e:
            self.error_occurred.emit("", f"Error: {e}")
            self.all_completed.emit(False)
