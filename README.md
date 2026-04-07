# CrankBoy Manager

A cross-platform desktop application for managing Game Boy ROMs on CrankBoy via USB/serial connection.

## Features

- **Drag-and-drop** file transfer - Simply drag ROM files onto the window
- **Folder support** - Drop a folder to automatically find all ROMs inside
- **Automatic compression** - .gb and .gbc files are compressed to GBZ for faster transfer
- **Automatic cover art download** - Matching cover art is automatically downloaded and transferred with each ROM
- **Batch transfers** - Queue up to 20 files at once
- **Progress tracking** - Visual progress bars for each file and overall transfer
- **Auto-restart** - Optionally restart CrankBoy after transfer to refresh library
- **Cross-platform** - Works on Windows, macOS, and Linux

## Installation

### Requirements

- Python 3.8 or higher
- PyQt6
- pyserial

### Install Dependencies

```bash
pip install -r requirements.txt
```

## Usage

### Run the Application

```bash
python main.py
```

### Transfer Files

1. **Connect your CrankBoy** via USB
2. **Select the serial port** from the dropdown (click Refresh if needed)
3. **Add files** by:
   - Dragging and dropping files/folders onto the window
   - Clicking "Add Files..." button
4. **Click "Start Transfer"**
5. **Wait for completion** - Files will automatically decompress on the device

### Options

- **Verbose** - Show detailed transfer log
- **Auto-restart** - Restart CrankBoy after all transfers complete

### Supported File Types

- `.gb` - Game Boy ROMs (automatically compressed)
- `.gbc` - Game Boy Color ROMs (automatically compressed)
- `.gbz` - Pre-compressed GBZ files (transferred as-is)

### Automatic Cover Art Download

When you transfer a ROM, the app will automatically:

1. Calculate the CRC32 of the ROM file
2. Look up the game in the CrankBoy database (based on CRC32)
3. Download the matching cover art from the CrankBoy covers repository
4. Transfer both the ROM and cover art to your device

**Notes:**

- Cover art is downloaded automatically - no configuration needed
- Covers are saved with the same basename as the ROM file (e.g., `MyGame.gb` → `MyGame.pdi`)
- If a cover is not found in the database or download fails, the ROM will still be transferred
- Covers are downloaded fresh each time (not cached locally)

## Building Standalone Executable

### Quick Build (Recommended)

#### Windows

```batch
build.bat
```

#### macOS / Linux

```bash
chmod +x build.sh
./build.sh
```

### Advanced Build Options

#### Python Build Script

```bash
# Standard build
python build.py

# Clean previous builds
python build.py --clean

# Install dependencies only
python build.py --install
```

### Build Outputs

After building, you'll find:

- **Windows:** `dist/CrankBoyManager.exe` + `dist/CrankBoyManger-1.0.0-windows.zip`
- **macOS:** `dist/CrankBoy Manager.app` + `dist/CrankBoyManager-1.0.0-macos.zip`
- **Linux:** `dist/crankboy-manager` + `dist/CrankBoyManager-1.0.0-linux.tar.gz`

### Distribution

The ZIP/tar.gz archives are ready for distribution. Users can:

1. Download the appropriate archive for their platform
2. Extract it
3. Run the executable (no Python installation required)

## Protocol

This GUI uses the `ft` (File Transfer) protocol:

- Fixed 177-byte chunks
- CRC16 verification per chunk
- Automatic GBZ compression/decompression
- Detailed error reporting

See the main CrankBoy repository for protocol documentation.

## License

Same as CrankBoy project
