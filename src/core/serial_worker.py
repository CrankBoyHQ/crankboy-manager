"""Serial worker thread for non-blocking file transfers.

This module provides a QThread-based worker that handles file transfers
in the background, allowing the UI to remain responsive.
"""

import time
import io
import base64
import zlib
import urllib.parse
import serial
from PyQt6.QtCore import QThread, pyqtSignal
from src.core.transfer_engine import (
    send_command, read_response, parse_response,
    get_file_info
)

# Transfer tuning constants
MIN_ACK_TIMEOUT = 0.5   # Lower bound for adaptive chunk retransmit timeout
MAX_ACK_TIMEOUT = 2.0   # Upper bound for adaptive chunk retransmit timeout
DRAIN_TIMEOUT = 0.05    # Serial read timeout while draining responses
MAX_RETRIES = 5         # Max retransmits per chunk before aborting


class TransferFatalError(Exception):
    """Fatal device error; aborts the current file transfer."""


class SerialWorker(QThread):
    """Worker thread for transferring files to CrankBoy."""

    # Signals
    file_started = pyqtSignal(str, int)  # filename, total_bytes
    file_progress = pyqtSignal(int, int)  # bytes_sent, total_bytes
    file_completed = pyqtSignal(str, bool, str)  # filename, success, message
    chunk_sent = pyqtSignal(int)  # chunk_number
    log_message = pyqtSignal(str)  # message for log
    error_occurred = pyqtSignal(str, str)  # filename, error_message
    all_completed = pyqtSignal(bool)  # all_successful
    cover_started = pyqtSignal(str, int)  # cover_filename, total_bytes
    cover_completed = pyqtSignal(str, bool, str)  # cover_filename, success, message
    
    def __init__(self, port, files_info, options=None):
        """
        Initialize worker.
        
        Args:
            port: Serial port name (e.g., 'COM3' or '/dev/ttyUSB0')
            files_info: List of file info dicts from get_file_info()
            options: Dict with options like 'verbose', 'restart', etc.
        """
        super().__init__()
        self.port = port
        self.files_info = files_info
        self.options = options or {}
        self._is_running = True
        self._current_serial = None
        self.verbose = self.options.get('verbose', False)
        self.restart = self.options.get('restart', False)
        self.use_sft = self.options.get('use_sft', False)
        self.download_cover_art = self.options.get('download_cover_art', True)
    
    def _log(self, message):
        """Log message only if verbose mode is enabled."""
        if self.verbose:
            self.log_message.emit(message)
    
    def stop(self):
        """Request thread to stop gracefully."""
        self._is_running = False
    
    def run(self):
        """Main transfer loop."""
        try:
            # Open serial port
            self._log(f"Connecting to {self.port}...")
            ser = serial.Serial(self.port, 115200, timeout=5)
            self._current_serial = ser
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            all_successful = True
            any_successful = False

            for file_info in self.files_info:
                if not self._is_running:
                    break

                filename = file_info['filename']
                rom_size = file_info['gbz_size']
                cover_data = file_info.get('cover_data') if self.download_cover_art else None
                cover_size = len(cover_data) if cover_data else 0
                combined_total = rom_size + cover_size

                self.file_started.emit(filename, combined_total)
                self.log_message.emit(f"Transferring: {filename}")

                # Transfer ROM file
                success, message = self._transfer_file(ser, file_info, combined_total)
                if not success:
                    all_successful = False
                    self.file_completed.emit(filename, False, message)
                    continue

                # Mark that at least one ROM succeeded
                any_successful = True

                # Transfer cover art if available
                cover_filename = file_info.get('cover_filename')
                if cover_data and cover_filename:
                    cover_success, cover_message = self._transfer_cover(ser, cover_data, cover_filename, combined_total, rom_size)
                    if not cover_success:
                        # Cover transfer failure doesn't mark overall as failed, but we log it
                        self.log_message.emit(f"  Cover transfer failed: {cover_filename} - {cover_message}")

                self.file_completed.emit(filename, True, message)

            # Restart if requested and at least one ROM succeeded
            if any_successful and self.restart and self._is_running:
                self.log_message.emit("Restarting CrankBoy...")
                send_command(ser, "cb:restart")
                time.sleep(0.5)  # Give time for restart to initiate
            # Otherwise, disable SFT overlay if it was enabled
            elif self.use_sft:
                # Always try to turn off SFT if it was enabled, 
                # even if we were stopped (unless we're restarting)
                self.log_message.emit("Disabling Serial File Transfer overlay...")
                send_command(ser, "cb:sft:off")
                # Use a short timeout for this final response
                response = read_response(ser, timeout=1.0)
                if response == "cb:sft:ok":
                    self.log_message.emit("SFT overlay disabled ✓")
                time.sleep(0.1)  # Small delay after disabling SFT

            ser.close()
            self.all_completed.emit(all_successful)

        except serial.SerialException as e:
            self.error_occurred.emit("", f"Serial error: {e}")
            self.all_completed.emit(False)
        except Exception as e:
            self.error_occurred.emit("", f"Error: {e}")
            self.all_completed.emit(False)
    
    def _transfer_file(self, ser, file_info, combined_total):
        """Transfer a single file using window-based pipelining."""
        filename = file_info['filename']
        gbz_data = file_info['gbz_data']
        gbz_size = file_info['gbz_size']
        gbz_crc = file_info['gbz_crc']
        original_filename = file_info['original_filename']
        original_crc = file_info['original_crc']
        
        try:
            # Send begin command
            encoded_filename = urllib.parse.quote(file_info.get('gbz_filename', filename), safe='')
            crc_hex = f"{gbz_crc:08X}"

            if original_filename and original_crc:
                encoded_original = urllib.parse.quote(original_filename, safe='')
                original_crc_hex = f"{original_crc:08X}"
                cmd = f"ft:b:{encoded_filename}:{gbz_size}:{crc_hex}:{encoded_original}:{original_crc_hex}"
            else:
                cmd = f"ft:b:{encoded_filename}:{gbz_size}:{crc_hex}"

            send_command(ser, cmd)

            # Wait for ready response (format: WWCC where WW=window, CC=chunk)
            ready_params = self._wait_for_response(ser, "r", timeout=5)
            if ready_params is None:
                return False, "Device not ready"

            window_size, chunk_size = self._parse_ready_params(ready_params)
            self._log(f"Window size: {window_size}, Chunk size: {chunk_size} bytes")

            return self._windowed_transfer(
                ser, gbz_data, crc_hex, window_size, chunk_size,
                progress_base=0, progress_total=combined_total
            )

        except Exception as e:
            return False, str(e)

    @staticmethod
    def _parse_ready_params(ready_params):
        """Parse ready response (WWCC hex) into (window_size, chunk_size)."""
        try:
            ready_code = int(ready_params, 16)
            return (ready_code >> 8) & 0xFF, ready_code & 0xFF
        except ValueError:
            return 4, 177

    def _windowed_transfer(self, ser, data, crc_hex, window_size, chunk_size,
                           progress_base, progress_total):
        """Transfer data chunks using window-based pipelining.

        The retransmit timeout adapts to the device's round-trip time
        (EWMA, TCP-style) between MIN_ACK_TIMEOUT and MAX_ACK_TIMEOUT,
        so fast devices retransmit quickly while slow devices are not
        hammered with spurious retransmits.

        Returns (success, message).
        """
        # Pre-chunk the data
        chunks = []
        stream = io.BytesIO(data)
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            chunks.append(chunk)
        total_chunks = len(chunks)

        # Window-based transfer state
        next_seq_to_send = 0
        highest_acked = -1
        in_flight = {}  # seq -> {'data', 'time', 'retries'}
        batch_size = 3  # Start conservative for adaptive batching
        successful_batches = 0

        # Adaptive timeout state (smoothed round-trip time).
        # Start at MAX: no RTT sample exists yet, and chunks retransmitted
        # before the first ACK cannot be sampled (Karn's rule), so starting
        # low would cause a spurious-retransmit cascade on slow devices.
        srtt = None
        ack_timeout = MAX_ACK_TIMEOUT

        def send_window():
            """Send chunks to fill the window."""
            nonlocal next_seq_to_send
            while next_seq_to_send < total_chunks and len(in_flight) < window_size:
                if not self._is_running:
                    return
                seq = next_seq_to_send
                chunk_data = chunks[seq]
                self._send_chunk_data(ser, seq, chunk_data)
                in_flight[seq] = {
                    'data': chunk_data,
                    'time': time.time(),
                    'retries': 0
                }
                next_seq_to_send += 1
                self.chunk_sent.emit(seq)

        def process_response(timeout):
            """Process one response (ACK or NACK).

            Returns True if a response was processed, None if the read
            timed out. Raises TransferFatalError on fatal device errors.
            """
            nonlocal highest_acked, next_seq_to_send, batch_size
            nonlocal successful_batches, srtt, ack_timeout

            response = read_response(ser, timeout=timeout)
            if not response:
                return None  # Timeout, not an error

            self._log(f"Response: {response}")

            proto, cmd, params = parse_response(response)
            if proto not in ("ft", "cb"):
                return True  # Not a protocol message we handle, but not an error

            if cmd == "a":
                # Cumulative ACK
                try:
                    ack_seq = int(params, 16)
                except ValueError:
                    return True  # Parse error but not fatal

                newly_acked = [seq for seq in in_flight if seq <= ack_seq]

                # Sample RTT from the highest newly-acked chunk that was never
                # retransmitted (Karn's rule) and update the adaptive timeout
                now = time.time()
                for seq in sorted(newly_acked, reverse=True):
                    if in_flight[seq]['retries'] == 0:
                        rtt = now - in_flight[seq]['time']
                        srtt = rtt if srtt is None else 0.875 * srtt + 0.125 * rtt
                        ack_timeout = min(MAX_ACK_TIMEOUT,
                                          max(MIN_ACK_TIMEOUT, 4 * srtt))
                        break

                # Remove all chunks up to and including ack_seq from in_flight
                for seq in newly_acked:
                    del in_flight[seq]
                if ack_seq > highest_acked:
                    highest_acked = ack_seq
                    # Adaptive batching: track successful batches
                    successful_batches += 1
                    if successful_batches >= 5 and batch_size < (window_size - 2):
                        batch_size += 1
                        successful_batches = 0
                        self._log(f"Batch size increased to {batch_size}")
                return True

            if cmd == "n":
                # NACK - immediate error, reset batching
                batch_size = 3
                successful_batches = 0

                if params:
                    parts = params.split(':')
                    try:
                        nack_seq = int(parts[0], 16)
                        nack_code = parts[1] if len(parts) > 1 else ""

                        if nack_code == "seq":
                            # Resync requested
                            for seq in list(in_flight.keys()):
                                if seq >= nack_seq:
                                    del in_flight[seq]
                            next_seq_to_send = nack_seq
                            self._log(f"Resyncing to chunk {nack_seq:04X}")
                        elif nack_code == "crc":
                            # CRC error, mark for retry
                            if nack_seq in in_flight:
                                in_flight[nack_seq]['retries'] += 1
                            self._log(f"CRC error for chunk {nack_seq:04X}")
                        elif nack_code in ("write", "size"):
                            # Fatal error - abort current transfer
                            raise TransferFatalError(f"Device error: {nack_code}")
                    except (ValueError, IndexError):
                        pass
                return True

            if cmd == "x":
                # Device error - abort current transfer
                raise TransferFatalError(f"Device error: {params}")

            return True  # Unknown command but not fatal

        # Main transfer loop
        while highest_acked < total_chunks - 1:
            if not self._is_running:
                return False, "User stopped"

            # Fill the window
            send_window()

            # Process responses (drain all available)
            if in_flight:
                try:
                    for _ in iter(lambda: process_response(DRAIN_TIMEOUT), None):
                        if not in_flight:
                            break
                except TransferFatalError as e:
                    return False, str(e)

                # Check for timed-out chunks
                current_time = time.time()
                timeouts = [(seq, info) for seq, info in in_flight.items()
                            if current_time - info['time'] > ack_timeout]

                if timeouts:
                    def retransmit(timed_out, selective=False):
                        """Retransmit chunks; returns error message or None."""
                        for seq, info in timed_out:
                            if info['retries'] >= MAX_RETRIES:
                                return f"Max retries exceeded for chunk {seq:04X}"
                            kind = "Selective retransmit" if selective else "Timeout, retransmitting"
                            self._log(f"{kind} chunk {seq:04X}")
                            self._send_chunk_data(ser, seq, info['data'])
                            in_flight[seq]['time'] = current_time
                            in_flight[seq]['retries'] += 1
                        return None

                    first_timeouts = [(seq, info) for seq, info in timeouts if info['retries'] == 0]

                    if first_timeouts:
                        # First timeout - just retransmit
                        error = retransmit(first_timeouts)
                        if error:
                            return False, error
                    else:
                        # Repeated timeouts - use selective retransmit
                        self._log("Querying device status for selective retransmit")
                        send_command(ser, "ft:s")
                        status_response = read_response(ser, timeout=1)

                        if status_response and status_response.startswith("ft:d:"):
                            _, _, params = parse_response(status_response)
                            if params:
                                parts = params.split(':')
                                if len(parts) >= 2:
                                    try:
                                        window_base = int(parts[0], 16)
                                        bitmap = int(parts[1], 16)

                                        missing = []
                                        highest_processed = -1

                                        for seq, info in timeouts:
                                            if seq < window_base:
                                                # Already processed
                                                if seq in in_flight:
                                                    del in_flight[seq]
                                                if seq > highest_processed:
                                                    highest_processed = seq
                                            elif window_base <= seq < window_base + window_size:
                                                bit_position = seq - window_base
                                                if bitmap & (1 << bit_position):
                                                    # Received
                                                    if seq in in_flight:
                                                        del in_flight[seq]
                                                else:
                                                    # In window but not received
                                                    missing.append((seq, info))
                                            else:
                                                # Ahead of window
                                                missing.append((seq, info))

                                        # Update highest_acked if needed
                                        if highest_processed > highest_acked:
                                            highest_acked = highest_processed

                                        # Retransmit missing chunks
                                        error = retransmit(missing, selective=True)
                                        if error:
                                            return False, error
                                    except ValueError:
                                        pass
                        else:
                            # No status response - fall back to plain retransmit
                            # so retries still count towards MAX_RETRIES
                            error = retransmit(timeouts)
                            if error:
                                return False, error
            else:
                time.sleep(0.01)

            # Update progress
            chunks_completed = highest_acked + 1
            bytes_sent = sum(len(chunks[i]) for i in range(min(chunks_completed, total_chunks)))
            self.file_progress.emit(progress_base + bytes_sent, progress_total)

        if not self._is_running:
            return False, "User stopped"

        # Send end command
        send_command(ser, f"ft:e:{crc_hex}")

        # Wait for OK
        ok_response = self._wait_for_response(ser, "o", timeout=10)
        if ok_response is None:
            return False, "Transfer not confirmed"

        return True, f"Saved as {ok_response}"

    def _send_chunk_data(self, ser, seq, chunk_data):
        """Send chunk data without waiting for response."""
        seq_hex = f"{seq:04X}"
        crc32 = zlib.crc32(chunk_data) & 0xFFFFFFFF
        crc16 = crc32 & 0xFFFF
        crc16_hex = f"{crc16:04X}"
        b64_data = base64.b64encode(chunk_data).decode('ascii')
        
        cmd = f"ft:c:{seq_hex}:{crc16_hex}:{b64_data}"
        send_command(ser, cmd)
    
    def _transfer_cover(self, ser, cover_data, cover_filename, combined_total, offset):
        """Transfer cover art to the covers directory.

        Covers are transferred using the same ft protocol but with a special
        path prefix to indicate they go in the covers/ directory.
        """
        cover_size = len(cover_data)
        cover_crc = zlib.crc32(cover_data) & 0xFFFFFFFF

        self.cover_started.emit(cover_filename, cover_size)
        self._log(f"Transferring cover: {cover_filename}")

        try:
            # Send begin command
            # The C code will automatically save .pdi files to the covers directory
            encoded_filename = urllib.parse.quote(cover_filename, safe='')
            crc_hex = f"{cover_crc:08X}"

            cmd = f"ft:b:{encoded_filename}:{cover_size}:{crc_hex}"
            send_command(ser, cmd)

            # Wait for ready response
            ready_params = self._wait_for_response(ser, "r", timeout=5)
            if ready_params is None:
                return False, "Device not ready"

            window_size, chunk_size = self._parse_ready_params(ready_params)
            self._log(f"Cover window size: {window_size}, chunk size: {chunk_size}")

            success, message = self._windowed_transfer(
                ser, cover_data, crc_hex, window_size, chunk_size,
                progress_base=offset, progress_total=combined_total
            )
            if success:
                self.cover_completed.emit(cover_filename, True, message)
            return success, message

        except Exception as e:
            return False, str(e)

    def _wait_for_response(self, ser, expected_cmd, timeout=5):
        """Wait for a specific response from device."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if not self._is_running:
                return None

            response = read_response(ser, timeout=0.5)
            if not response:
                continue

            proto, cmd, params = parse_response(response)
            if proto in ("ft", "cb"):
                if cmd == "x":
                    self.log_message.emit(f"Device error: {params}")
                    return None
                elif cmd == expected_cmd:
                    return params
                elif expected_cmd is None:
                    return cmd

        return None
