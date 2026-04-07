#!/usr/bin/env python3
"""
CrankBoy Manager
A cross-platform desktop application for managing Game Boy ROMs on CrankBoy.
"""

import sys


def main():
    # Import here to avoid loading PyQt6 when importing submodules
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt
    from src.ui.main_window import MainWindow
    from src.utils.settings import Settings
    from src.version import VERSION

    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("CrankBoy Manager")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("CrankBoy Dev Team")

    # Load settings
    settings = Settings()

    # Create and show main window
    window = MainWindow(settings)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
