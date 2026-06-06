"""Settings management for CrankBoy Manager."""

from PyQt6.QtCore import QLocale, QSettings


def _default_download_cover_art():
    """Default value for the download-cover-art setting, based on the
    host system locale."""
    name = QLocale.system().name()  # e.g. "en_US", "ja_JP", "ko_KR"
    parts = name.replace("-", "_").split("_")
    language = parts[0].lower() if parts else ""
    territory = parts[1].upper() if len(parts) > 1 else ""
    if language in ("ja", "ko") or territory in ("JP", "KR", "KP"):
        return False
    return True


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
    
    def get_download_cover_art(self):
        """Get download-cover-art setting."""
        return self._settings.value(
            "transfer/download_cover_art",
            _default_download_cover_art(),
            type=bool,
        )

    def set_download_cover_art(self, enabled):
        """Save download-cover-art setting."""
        self._settings.setValue("transfer/download_cover_art", enabled)

    def get_window_geometry(self):
        """Get window geometry."""
        return self._settings.value("window/geometry")
    
    def set_window_geometry(self, geometry):
        """Save window geometry."""
        self._settings.setValue("window/geometry", geometry)

    def get_log_visible(self):
        """Get log visibility setting."""
        return self._settings.value("window/log_visible", True, type=bool)

    def set_log_visible(self, visible):
        """Save log visibility setting."""
        self._settings.setValue("window/log_visible", visible)
