"""Background cover download worker for downloading covers in parallel."""

import os
import time
import queue
import threading
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.database import database as rom_database
from src.core.cover_downloader import download_cover


class CoverDownloadWorker(QThread):
    """Worker thread for downloading cover art in the background with persistent queue."""

    # Signals
    cover_started = pyqtSignal(str, str)  # rom_filename, cover_filename
    cover_progress = pyqtSignal(str, int, int)  # rom_filename, current_bytes, total_bytes
    cover_completed = pyqtSignal(str, bool, str)  # rom_filename, success, message
    all_completed = pyqtSignal()  # All downloads completed (queue empty)

    def __init__(self, max_concurrent=3):
        """Initialize worker with empty queue.

        Args:
            max_concurrent: Maximum number of parallel downloads
        """
        super().__init__()
        self.max_concurrent = max_concurrent
        self._is_running = True
        self._completed_count = 0
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._queue_lock = threading.Lock()
        self._active_count = 0

    def add_to_queue(self, file_info_list):
        """Thread-safe method to add files to download queue.

        Args:
            file_info_list: List of file info dicts that need covers downloaded
        """
        added_count = 0
        with self._queue_lock:
            for file_info in file_info_list:
                crc = file_info.get('original_crc')
                # Skip if already downloaded or no CRC
                if crc and file_info.get('cover_data') is None:
                    self._queue.put(file_info)
                    added_count += 1
        return added_count

    def stop(self):
        """Request thread to stop gracefully after queue is processed."""
        self._is_running = False

    def has_work(self):
        """Check if worker has actual work to do (queue not empty or active downloads)."""
        with self._queue_lock:
            queue_not_empty = not self._queue.empty()
        with self._lock:
            has_active = self._active_count > 0
        return queue_not_empty or has_active

    def _download_single(self, file_info):
        """Download cover for a single file."""
        rom_filename = file_info['filename']
        crc = file_info.get('original_crc')

        if not crc:
            return False, "No CRC available"

        # Get cover info from database
        cover_info = rom_database.get_cover_info(crc)
        if not cover_info:
            return False, "Not in database"

        cover_filename = cover_info['filename']

        # Emit started signal
        self.cover_started.emit(rom_filename, cover_filename)

        # Try downloading (with retries)
        success = False
        last_error = "Unknown error"

        for attempt in range(1, 4):  # 3 attempts
            if not self._is_running:
                break

            try:
                # Pass cancellation lambda to allow downloader to abort mid-stream
                cover_data = download_cover(
                    crc,
                    progress_callback=lambda curr, total, rf=rom_filename: self.cover_progress.emit(rf, curr, total),
                    is_running_func=lambda: self._is_running
                )

                if cover_data:
                    # Store cover data in file_info
                    file_info['cover_data'] = cover_data
                    # Construct cover filename from ROM basename
                    rom_basename = os.path.splitext(rom_filename)[0]
                    file_info['cover_filename'] = f"{rom_basename}.pdi"
                    file_info['cover_url'] = cover_info['url']
                    success = True
                    last_error = f"Downloaded {len(cover_data)} bytes"
                    break
                else:
                    last_error = "Download returned no data or failed"
                    if attempt < 3:
                        time.sleep(1.0)

            except Exception as e:
                last_error = str(e)
                if attempt < 3:
                    time.sleep(1.0)

        self.cover_completed.emit(rom_filename, success, last_error)

        with self._lock:
            self._completed_count += 1
            self._active_count -= 1

        return success, last_error

    def run(self):
        """Main loop - continuously process queue until stopped."""
        self._lock = threading.Lock()
        active_threads = {}

        while self._is_running:
            # Start new downloads up to max_concurrent
            while len(active_threads) < self.max_concurrent and self._is_running:
                try:
                    # Non-blocking get with timeout to check _is_running periodically
                    file_info = self._queue.get(timeout=0.1)
                except queue.Empty:
                    break

                rom_filename = file_info['filename']

                with self._lock:
                    self._active_count += 1

                # Start download in a thread
                thread = threading.Thread(
                    target=self._download_single,
                    args=(file_info,),
                    daemon=True
                )
                active_threads[rom_filename] = thread
                thread.start()

            # Check for completed downloads
            finished = []
            for rom_filename, thread in list(active_threads.items()):
                if not thread.is_alive():
                    finished.append(rom_filename)

            for rom_filename in finished:
                del active_threads[rom_filename]

            # Emit all_completed when queue is empty and no active downloads
            if self._queue.empty() and not active_threads:
                # Only emit if we had some activity (don't emit on startup)
                with self._lock:
                    if self._completed_count > 0 or self._active_count > 0:
                        self.all_completed.emit()
                        self._completed_count = 0  # Reset for next batch

            # Sleep when idle to prevent busy-waiting
            if self._queue.empty() and len(active_threads) == 0:
                time.sleep(0.5)
            else:
                time.sleep(0.1)  # Small sleep when active

        # Wait for any remaining downloads to complete
        for thread in active_threads.values():
            thread.join(timeout=2.0)

        # Only emit all_completed if something was actually downloaded
        with self._lock:
            if self._completed_count > 0:
                self.all_completed.emit()
