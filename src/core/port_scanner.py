"""Serial port scanner for detecting CrankBoy devices with ping protocol."""

import time
import serial
import serial.tools.list_ports
from src.core.transfer_engine import send_command, read_response

# Cache for last known CrankBoy port
_last_crankboy_port = None

# Playdate USB Vendor/Product IDs (for detection)
PLAYDATE_VENDOR_ID = 0x1331
PLAYDATE_PRODUCT_ID_SERIAL = 0x5740


def test_port(port_name, timeout=1.5, should_stop_callback=None):
    """Test if a serial port is a responsive CrankBoy device.

    Args:
        port_name: Serial port device name (e.g., 'COM3' or '/dev/ttyUSB0')
        timeout: How long to wait for response in seconds
        should_stop_callback: Optional callback that returns True if scan should stop

    Returns:
        tuple: (is_crankboy, version_info)
            - is_crankboy: True if CrankBoy responded to ping
            - version_info: Version string from response or None
    """
    try:
        with serial.Serial(port_name, 115200, timeout=timeout) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            # Send ping command (protocol: msg cb:ping)
            send_command(ser, "cb:ping")

            # Wait for response
            start_time = time.time()
            while time.time() - start_time < timeout:
                # Check if we should stop
                if should_stop_callback and should_stop_callback():
                    return False, None

                response = read_response(ser, timeout=0.5)
                if response and response.startswith("cb:pong"):
                    # Parse version from response: cb:pong:CrankBoy:v1.0.0
                    parts = response.split(':')
                    version = parts[3] if len(parts) > 3 else "unknown"
                    return True, version
            return False, None
    except Exception:
        return False, None


def is_playdate_device(port):
    """Check if a port is likely a Playdate device by VID/PID.
    
    Args:
        port: serial.tools.list_ports.ListPortInfo object
        
    Returns:
        bool: True if likely Playdate device, False otherwise.
    """
    # Check vid and pid attributes
    vid = getattr(port, 'vid', None)
    pid = getattr(port, 'pid', None)
    
    if vid is not None and pid is not None:
        return vid == PLAYDATE_VENDOR_ID and pid == PLAYDATE_PRODUCT_ID_SERIAL
    
    # Fallback: check hardware ID string (Windows)
    hwid = getattr(port, 'hwid', '')
    if hwid:
        vid_str = f"VID_{PLAYDATE_VENDOR_ID:04X}"
        pid_str = f"PID_{PLAYDATE_PRODUCT_ID_SERIAL:04X}"
        if vid_str in hwid.upper() and pid_str in hwid.upper():
            return True
    
    return False


def scan_for_crankboy(should_stop_callback=None):
    """Scan for CrankBoy and return detailed status.

    Args:
        should_stop_callback: Optional callback that returns True if scan should stop

    Returns:
        dict with keys:
            - 'status': 'connected_running', 'connected_not_running', or 'not_connected'
            - 'port': dict with device info if found, None otherwise
            - 'message': Human-readable status message
    """
    global _last_crankboy_port

    # Get all serial ports
    all_ports = list(serial.tools.list_ports.comports())

    # Filter out Bluetooth devices
    usb_ports = [p for p in all_ports if 'bluetooth' not in p.description.lower()]

    # Check if any Playdate devices are connected (by VID/PID)
    playdate_ports = [p for p in usb_ports if is_playdate_device(p)]

    # Test cached port first
    if _last_crankboy_port:
        # Check if we should stop before testing
        if should_stop_callback and should_stop_callback():
            return {
                'status': 'not_connected',
                'port': None,
                'message': "Scan interrupted"
            }

        is_crankboy, version = test_port(_last_crankboy_port, timeout=1.0, should_stop_callback=should_stop_callback)
        if is_crankboy:
            for p in usb_ports:
                if p.device == _last_crankboy_port:
                    return {
                        'status': 'connected_running',
                        'port': {
                            'device': p.device,
                            'description': p.description,
                            'version': version
                        },
                        'message': f"CrankBoy detected on {p.device}"
                    }
        _last_crankboy_port = None

    # Test all ports for CrankBoy response
    for port in usb_ports:
        # Check if we should stop before testing each port
        if should_stop_callback and should_stop_callback():
            return {
                'status': 'not_connected',
                'port': None,
                'message': "Scan interrupted"
            }

        is_crankboy, version = test_port(port.device, should_stop_callback=should_stop_callback)
        if is_crankboy:
            _last_crankboy_port = port.device
            return {
                'status': 'connected_running',
                'port': {
                    'device': port.device,
                    'description': port.description,
                    'version': version
                },
                'message': f"CrankBoy detected on {port.device}"
            }

    # No CrankBoy response - check if Playdate hardware is connected
    if playdate_ports:
        return {
            'status': 'connected_not_running',
            'port': None,
            'message': "Playdate connected but CrankBoy not running - please start CrankBoy"
        }

    # No USB devices at all
    if not usb_ports:
        return {
            'status': 'not_connected',
            'port': None,
            'message': "Playdate not connected - please connect your device"
        }

    # USB devices present but none are Playdate
    return {
        'status': 'not_connected',
        'port': None,
        'message': "Playdate not connected - please connect your device"
    }


def find_crankboy_port():
    """Find CrankBoy port, testing cached port first.
    
    Returns:
        tuple: (port_name, version) or (None, None) if not found
    """
    result = scan_for_crankboy()
    if result['status'] == 'connected_running' and result['port']:
        return result['port']['device'], result['port']['version']
    return None, None


def scan_ports():
    """Scan and return only responsive CrankBoy devices.
    
    Returns:
        list: List of dicts with keys 'device', 'description', 'version'
              Empty list if no CrankBoy found
    """
    result = scan_for_crankboy()
    if result['status'] == 'connected_running' and result['port']:
        return [result['port']]
    return []


def clear_cached_port():
    """Clear the cached CrankBoy port."""
    global _last_crankboy_port
    _last_crankboy_port = None
