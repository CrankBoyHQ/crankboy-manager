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

                # Transfer ROM file
                success = self._transfer_file(ser, file_info)
                if not success:
                    all_successful = False
                    continue  # Don't try to transfer cover if ROM failed

                # Mark that at least one ROM succeeded
                any_successful = True

                # Transfer cover art if available (doesn't affect success status)
                cover_data = file_info.get('cover_data')
                cover_filename = file_info.get('cover_filename')
                if cover_data and cover_filename:
                    cover_success = self._transfer_cover(ser, cover_data, cover_filename)
                    if not cover_success:
                        # Cover transfer failure doesn't mark overall as failed
                        self.log_message.emit(f"  Cover transfer failed: {cover_filename}")

            # Restart if requested and at least one ROM succeeded
            if any_successful and self.restart and self._is_running:
                self.log_message.emit("Restarting CrankBoy...")
                send_command(ser, "cb:restart")
                time.sleep(0.5)  # Give time for restart to initiate
            # Otherwise, disable SFT overlay if it was enabled
            elif self.use_sft and self._is_running:
                self.log_message.emit("Disabling Serial File Transfer overlay...")
                send_command(ser, "cb:sft:off")
                response = read_response(ser, timeout=2.0)
                if response == "cb:sft:ok":
                    self.log_message.emit("SFT overlay disabled ✓")
                else:
                    self.log_message.emit(f"Warning: Failed to disable SFT overlay: {response}")
                time.sleep(0.1)  # Small delay after disabling SFT

            ser.close()
            self.all_completed.emit(all_successful)

        except serial.SerialException as e:
            self.error_occurred.emit("", f"Serial error: {e}")
            self.all_completed.emit(False)
        except Exception as e:
            self.error_occurred.emit("", f"Error: {e}")
            self.all_completed.emit(False)
    
    def _transfer_file(self, ser, file_info):
        """Transfer a single file using window-based pipelining."""
        filename = file_info['filename']
        gbz_data = file_info['gbz_data']
        gbz_size = file_info['gbz_size']
        gbz_crc = file_info['gbz_crc']
        original_filename = file_info['original_filename']
        original_crc = file_info['original_crc']
        
        self.file_started.emit(filename, gbz_size)
        self.log_message.emit(f"Transferring: {filename}")

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
                self.file_completed.emit(filename, False, "Device not ready")
                return False

            # Parse window size and chunk size
            try:
                ready_code = int(ready_params, 16)
                window_size = (ready_code >> 8) & 0xFF
                chunk_size = ready_code & 0xFF
            except ValueError:
                window_size = 4
                chunk_size = 177

            self._log(f"Window size: {window_size}, Chunk size: {chunk_size} bytes")
            
            # Pre-chunk the data
            chunks = []
            gbz_stream = io.BytesIO(gbz_data)
            while True:
                chunk = gbz_stream.read(chunk_size)
                if not chunk:
                    break
                chunks.append(chunk)
            total_chunks = len(chunks)
            
            # Window-based transfer state
            next_seq_to_send = 0
            highest_acked = -1
            in_flight = {}  # seq -> (chunk_data, send_time, retry_count)
            batch_size = 3  # Start conservative for adaptive batching
            successful_batches = 0
            
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
            
            def process_response(timeout=0.1):
                """Process one response (ACK or NACK). Returns True if processed, None if timeout, False on fatal error."""
                nonlocal highest_acked, next_seq_to_send, batch_size, successful_batches
                
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
                        # Remove all chunks up to and including ack_seq from in_flight
                        for seq in list(in_flight.keys()):
                            if seq <= ack_seq:
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
                    except ValueError:
                        return True  # Parse error but not fatal
                
                elif cmd == "n":
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
                                # Fatal error - abort current file
                                self.file_completed.emit(filename, False, f"Device error: {nack_code}")
                                return False
                        except (ValueError, IndexError):
                            pass
                    return True
                
                elif cmd == "x":
                    # Device error - abort current file
                    self.file_completed.emit(filename, False, f"Device error: {params}")
                    return False
                
                return True  # Unknown command but not fatal
            
            # Main transfer loop
            while highest_acked < total_chunks - 1:
                if not self._is_running:
                    return False
                
                # Fill the window
                send_window()
                
                # Process responses
                if in_flight:
                    process_response(timeout=0.2)
                    # Continue regardless of response - timeouts are handled below
                    
                    # Check for timeouts
                    current_time = time.time()
                    timeouts = [(seq, info) for seq, info in in_flight.items() 
                               if current_time - info['time'] > 0.5]
                    
                    if timeouts:
                        first_timeouts = [(seq, info) for seq, info in timeouts if info['retries'] == 0]
                        
                        if first_timeouts:
                            # First timeout - just retransmit
                            for seq, info in first_timeouts:
                                if info['retries'] < 5:
                                    self._log(f"Timeout, retransmitting chunk {seq:04X}")
                                    self._send_chunk_data(ser, seq, info['data'])
                                    in_flight[seq]['time'] = current_time
                                    in_flight[seq]['retries'] += 1
                                else:
                                    self.file_completed.emit(filename, False, f"Max retries exceeded for chunk {seq:04X}")
                                    return False
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
                                            for seq, info in missing:
                                                if info['retries'] < 5:
                                                    self._log(f"Selective retransmit chunk {seq:04X}")
                                                    self._send_chunk_data(ser, seq, info['data'])
                                                    in_flight[seq]['time'] = current_time
                                                    in_flight[seq]['retries'] += 1
                                                else:
                                                    self.file_completed.emit(filename, False, f"Max retries exceeded for chunk {seq:04X}")
                                                    return False
                                        except ValueError:
                                            pass
                else:
                    time.sleep(0.01)
                
                # Update progress
                chunks_completed = highest_acked + 1
                bytes_sent = sum(len(chunks[i]) for i in range(min(chunks_completed, total_chunks)))
                self.file_progress.emit(bytes_sent, gbz_size)
            
            if not self._is_running:
                return False
            
            # Send end command
            send_command(ser, f"ft:e:{crc_hex}")
            
            # Wait for OK
            ok_response = self._wait_for_response(ser, "o", timeout=10)
            if ok_response is None:
                self.file_completed.emit(filename, False, "Transfer not confirmed")
                return False
            
            self.file_completed.emit(filename, True, f"Saved as {ok_response}")
            return True
            
        except Exception as e:
            self.file_completed.emit(filename, False, str(e))
            return False
    
    def _send_chunk_data(self, ser, seq, chunk_data):
        """Send chunk data without waiting for response."""
        seq_hex = f"{seq:04X}"
        crc32 = zlib.crc32(chunk_data) & 0xFFFFFFFF
        crc16 = crc32 & 0xFFFF
        crc16_hex = f"{crc16:04X}"
        b64_data = base64.b64encode(chunk_data).decode('ascii')
        
        cmd = f"ft:c:{seq_hex}:{crc16_hex}:{b64_data}"
        send_command(ser, cmd)
    
    def _transfer_cover(self, ser, cover_data, cover_filename):
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
                self.cover_completed.emit(cover_filename, False, "Device not ready")
                return False

            # Parse window size and chunk size
            try:
                ready_code = int(ready_params, 16)
                window_size = (ready_code >> 8) & 0xFF
                chunk_size = ready_code & 0xFF
            except ValueError:
                window_size = 4
                chunk_size = 177

            self._log(f"Cover window size: {window_size}, chunk size: {chunk_size}")

            # Pre-chunk the data
            chunks = []
            cover_stream = io.BytesIO(cover_data)
            while True:
                chunk = cover_stream.read(chunk_size)
                if not chunk:
                    break
                chunks.append(chunk)
            total_chunks = len(chunks)

            # Window-based transfer state
            next_seq_to_send = 0
            highest_acked = -1
            in_flight = {}
            batch_size = 3
            successful_batches = 0

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

            def process_response(timeout=0.1):
                """Process one response."""
                nonlocal highest_acked, batch_size, successful_batches

                response = read_response(ser, timeout=timeout)
                if not response:
                    return None

                self._log(f"Cover response: {response}")

                proto, cmd, params = parse_response(response)
                if proto not in ("ft", "cb"):
                    return True

                if cmd == "a":
                    try:
                        ack_seq = int(params, 16)
                        for seq in list(in_flight.keys()):
                            if seq <= ack_seq:
                                del in_flight[seq]
                        if ack_seq > highest_acked:
                            highest_acked = ack_seq
                            successful_batches += 1
                            if successful_batches >= 5 and batch_size < (window_size - 2):
                                batch_size += 1
                                successful_batches = 0
                        return True
                    except ValueError:
                        return True

                elif cmd == "n":
                    batch_size = 3
                    successful_batches = 0
                    if params:
                        parts = params.split(':')
                        try:
                            nack_seq = int(parts[0], 16)
                            nack_code = parts[1] if len(parts) > 1 else ""
                            if nack_code == "seq":
                                for seq in list(in_flight.keys()):
                                    if seq >= nack_seq:
                                        del in_flight[seq]
                                nonlocal next_seq_to_send
                                next_seq_to_send = nack_seq
                            elif nack_code == "crc":
                                if nack_seq in in_flight:
                                    in_flight[nack_seq]['retries'] += 1
                            elif nack_code in ("write", "size"):
                                # Fatal error - abort current cover
                                self.cover_completed.emit(cover_filename, False, f"Device error: {nack_code}")
                                return False
                        except (ValueError, IndexError):
                            pass
                    return True

                elif cmd == "x":
                    # Device error - abort current cover
                    self.cover_completed.emit(cover_filename, False, f"Device error: {params}")
                    return False

                return True

            # Main transfer loop
            while highest_acked < total_chunks - 1:
                if not self._is_running:
                    return False

                send_window()

                if in_flight:
                    process_response(timeout=0.2)

                    current_time = time.time()
                    timeouts = [(seq, info) for seq, info in in_flight.items()
                               if current_time - info['time'] > 0.5]

                    if timeouts:
                        first_timeouts = [(seq, info) for seq, info in timeouts if info['retries'] == 0]

                        if first_timeouts:
                            for seq, info in first_timeouts:
                                if info['retries'] < 5:
                                    self._log(f"Cover timeout, retransmitting chunk {seq:04X}")
                                    self._send_chunk_data(ser, seq, info['data'])
                                    in_flight[seq]['time'] = current_time
                                    in_flight[seq]['retries'] += 1
                                else:
                                    self.cover_completed.emit(cover_filename, False, f"Max retries exceeded for chunk {seq:04X}")
                                    return False
                        else:
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
                                                    if seq in in_flight:
                                                        del in_flight[seq]
                                                    if seq > highest_processed:
                                                        highest_processed = seq
                                                elif window_base <= seq < window_base + window_size:
                                                    bit_position = seq - window_base
                                                    if bitmap & (1 << bit_position):
                                                        if seq in in_flight:
                                                            del in_flight[seq]
                                                    else:
                                                        missing.append((seq, info))
                                                else:
                                                    missing.append((seq, info))

                                            if highest_processed > highest_acked:
                                                highest_acked = highest_processed

                                            for seq, info in missing:
                                                if info['retries'] < 5:
                                                    self._log(f"Cover selective retransmit chunk {seq:04X}")
                                                    self._send_chunk_data(ser, seq, info['data'])
                                                    in_flight[seq]['time'] = current_time
                                                    in_flight[seq]['retries'] += 1
                                                else:
                                                    self.cover_completed.emit(cover_filename, False, f"Max retries exceeded for chunk {seq:04X}")
                                                    return False
                                        except ValueError:
                                            pass
                else:
                    time.sleep(0.01)

                # Update progress (reuse file_progress signal)
                chunks_completed = highest_acked + 1
                bytes_sent = sum(len(chunks[i]) for i in range(min(chunks_completed, total_chunks)))
                # Don't emit progress for cover to avoid confusing the main progress bar

            if not self._is_running:
                return False

            # Send end command
            send_command(ser, f"ft:e:{crc_hex}")

            # Wait for OK
            ok_response = self._wait_for_response(ser, "o", timeout=10)
            if ok_response is None:
                self.cover_completed.emit(cover_filename, False, "Transfer not confirmed")
                return False

            self.cover_completed.emit(cover_filename, True, f"Saved as {ok_response}")
            return True

        except Exception as e:
            self.cover_completed.emit(cover_filename, False, str(e))
            return False

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
