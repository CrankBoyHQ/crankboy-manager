"""Port scanner worker thread for non-blocking CrankBoy detection."""

from PyQt6.QtCore import QThread, pyqtSignal
from src.core.port_scanner import scan_for_crankboy


class PortScannerWorker(QThread):
    """Worker thread for scanning ports without blocking UI."""
    
    # Signals
    scan_complete = pyqtSignal(dict)  # Result dict from scan_for_crankboy
    
    def __init__(self):
        super().__init__()
        self._is_running = False
    
    def run(self):
        """Run the port scan in background."""
        self._is_running = True
        result = scan_for_crankboy()
        if self._is_running:
            self.scan_complete.emit(result)
    
    def stop(self):
        """Request thread to stop."""
        self._is_running = False
