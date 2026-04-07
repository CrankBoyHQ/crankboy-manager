"""Background cover download worker for downloading covers in parallel."""

import os
import time
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.database import database as rom_database
from src.core.cover_downloader import download_cover


class CoverDownloadWorker(QThread):
    """Worker thread for downloading cover art in the background."""

    # Signals
    cover_started = pyqtSignal(str, str)  # rom_filename, cover_filename
    cover_progress = pyqtSignal(str, int, int)  # rom_filename, current_bytes, total_bytes
    cover_completed = pyqtSignal(str, bool, str)  # rom_filename, success, message
    all_completed = pyqtSignal()  # All downloads completed

    def __init__(self, file_info_list, max_concurrent=3):
        """Initialize worker.

        Args:
            file_info_list: List of file info dicts that need covers downloaded
            max_concurrent: Maximum number of parallel downloads
        """
        super().__init__()
        self.file_info_list = file_info_list
        self.max_concurrent = max_concurrent
        self._is_running = True
        self._completed_count = 0
        self._lock = None  # Will be created in run()

    def stop(self):
        """Request thread to stop gracefully."""
        self._is_running = False

    def run(self):
        """Main download loop with parallel downloads."""
        import threading
        self._lock = threading.Lock()

        if not self.file_info_list:
            self.all_completed.emit()
            return

        # Create a queue of files that need cover downloads
        download_queue = []
        for file_info in self.file_info_list:
            # Check if we have CRC and need to download
            crc = file_info.get('original_crc')
            # Skip if already downloaded (cover_data exists and is not None)
            if crc and file_info.get('cover_data') is None:
                download_queue.append(file_info)

        if not download_queue:
            self.all_completed.emit()
            return

        # Track active downloads
        active_downloads = {}
        completed_downloads = {}

        def download_single(file_info):
            """Download cover for a single file."""
            rom_filename = file_info['filename']
            crc = file_info.get('original_crc')

            if not crc:
                with self._lock:
                    completed_downloads[rom_filename] = (False, "No CRC available")
                return

            # Get cover info from database
            cover_info = rom_database.get_cover_info(crc)
            if not cover_info:
                with self._lock:
                    completed_downloads[rom_filename] = (False, "Not in database")
                return

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

            with self._lock:
                completed_downloads[rom_filename] = (success, last_error)

            self.cover_completed.emit(rom_filename, success, last_error)

        # Process queue with parallel downloads
        queue_idx = 0

        while self._is_running:
            # Start new downloads up to max_concurrent
            while (len(active_downloads) < self.max_concurrent and
                   queue_idx < len(download_queue) and
                   self._is_running):

                file_info = download_queue[queue_idx]
                rom_filename = file_info['filename']

                # Skip if already completed
                if rom_filename in completed_downloads:
                    queue_idx += 1
                    continue

                # Start download in a thread
                thread = threading.Thread(
                    target=download_single,
                    args=(file_info,),
                    daemon=True
                )
                active_downloads[rom_filename] = thread
                thread.start()
                queue_idx += 1

            # Check for completed downloads
            finished = []
            for rom_filename, thread in list(active_downloads.items()):
                if not thread.is_alive():
                    finished.append(rom_filename)

            for rom_filename in finished:
                del active_downloads[rom_filename]
                self._completed_count += 1

            # Check if all done
            if not active_downloads and queue_idx >= len(download_queue):
                break

            # Small sleep to prevent busy-waiting
            time.sleep(0.1)

        # Wait for any remaining downloads with a shorter timeout
        # They should exit quickly anyway because we set _is_running=False
        for thread in active_downloads.values():
            thread.join(timeout=1.0)

        self.all_completed.emit()
