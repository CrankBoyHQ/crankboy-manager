"""Settings management for CrankBoy Manager."""

from PyQt6.QtCore import QSettings


class Settings:
    """Manages application settings persistence."""
    
    def __init__(self):
        self._settings = QSettings()
    
    def get_verbose(self):
        """Get verbose mode setting."""
        return self._settings.value("transfer/verbose", False, type=bool)
    
    def set_verbose(self, verbose):
        """Save verbose mode setting."""
        self._settings.setValue("transfer/verbose", verbose)
    
    def get_auto_restart(self):
        """Get auto-restart setting."""
        return self._settings.value("transfer/auto_restart", True, type=bool)
    
    def set_auto_restart(self, auto_restart):
        """Save auto-restart setting."""
        self._settings.setValue("transfer/auto_restart", auto_restart)
    
    def get_keep_compressed(self):
        """Get keep files compressed on device setting."""
        return self._settings.value("transfer/keep_compressed", False, type=bool)
    
    def set_keep_compressed(self, keep_compressed):
        """Save keep files compressed on device setting."""
        self._settings.setValue("transfer/keep_compressed", keep_compressed)
    
    def get_window_geometry(self):
        """Get window geometry."""
        return self._settings.value("window/geometry")
    
    def set_window_geometry(self, geometry):
        """Save window geometry."""
        self._settings.setValue("window/geometry", geometry)
