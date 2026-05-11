"""Serial port scanner for detecting CrankBoy devices with ping protocol."""

import errno
import os
import sys
import time
import serial
import serial.tools.list_ports
from src.core.transfer_engine import send_command, read_response

# Cache for last known CrankBoy port
_last_crankboy_port = None

# Playdate USB Vendor/Product IDs (for detection)
PLAYDATE_VENDOR_ID = 0x1331
PLAYDATE_PRODUCT_ID_SERIAL = 0x5740


def _is_permission_error(exc):
    """True if a SerialException/OSError indicates EACCES."""
    if isinstance(exc, PermissionError):
        return True
    cause = getattr(exc, '__cause__', None)
    if isinstance(cause, PermissionError):
        return True
    if getattr(cause, 'errno', None) == errno.EACCES:
        return True
    if getattr(exc, 'errno', None) == errno.EACCES:
        return True
    return 'permission denied' in str(exc).lower()


def _linux_port_group(port_device):
    """On Linux, return the group name owning the device node, or None."""
    if sys.platform != 'linux':
        return None
    try:
        import grp
        gid = os.stat(port_device).st_gid
        return grp.getgrgid(gid).gr_name
    except (OSError, KeyError, ImportError):
        return None


def _build_permission_message(port_device):
    """Build a user-facing message for a Playdate port we cannot open."""
    lines = [
        f"Playdate detected on {port_device} but the port cannot be opened.",
    ]
    group = _linux_port_group(port_device)
    if group:
        lines.append(f'User may need to be added to the group "{group}"')
        lines.append(f"  sudo usermod -aG {group} $USER")
        lines.append("then reboot your computer")
    return "\n".join(lines)


def test_port(port_name, timeout=1.5, should_stop_callback=None):
    """Test if a serial port is a responsive CrankBoy device.

    Args:
        port_name: Serial port device name (e.g., 'COM3' or '/dev/ttyUSB0')
        timeout: How long to wait for response in seconds
        should_stop_callback: Optional callback that returns True if scan should stop

    Returns:
        tuple: (status, version_info)
            - status: 'crankboy' if CrankBoy responded to ping,
                      'permission_denied' if the port could not be opened,
                      False otherwise
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
                    return 'crankboy', version
            return False, None
    except (serial.SerialException, OSError) as e:
        if _is_permission_error(e):
            return 'permission_denied', None
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
    """Scan for all CrankBoy devices and return detailed status.

    Args:
        should_stop_callback: Optional callback that returns True if scan should stop

    Returns:
        dict with keys:
            - 'status': 'connected_running', 'connected_not_running',
                        'not_accessible', or 'not_connected'
            - 'ports': list of dicts with device info for every detected
                       Playdate port. Each dict has:
                         'device'      - serial device path
                         'description' - description from list_ports
                         'version'     - CrankBoy version string, or None
                                         if CrankBoy is not running / unknown
                         'accessible'  - False if the port could not be
                                         opened (permission denied)
            - 'message': Human-readable status message
    """
    global _last_crankboy_port

    # Get all serial ports
    all_ports = list(serial.tools.list_ports.comports())

    # Filter out Bluetooth devices
    usb_ports = [p for p in all_ports if 'bluetooth' not in p.description.lower()]

    # Seed an entry for every Playdate detected by VID/PID so it appears
    # in the result even if its port can't be opened or doesn't respond.
    playdate_info = {}
    for p in usb_ports:
        if is_playdate_device(p):
            playdate_info[p.device] = {
                'device': p.device,
                'description': p.description,
                'version': None,
                'accessible': True,
            }

    responsive_count = 0
    inaccessible_count = 0

    # Test all ports for CrankBoy response
    for port in usb_ports:
        # Check if we should stop before testing each port
        if should_stop_callback and should_stop_callback():
            return {
                'status': 'not_connected',
                'ports': [],
                'message': "Scan interrupted"
            }

        status, version = test_port(port.device, should_stop_callback=should_stop_callback)
        if status == 'crankboy':
            # Record even if VID/PID didn't classify it as Playdate
            entry = playdate_info.setdefault(port.device, {
                'device': port.device,
                'description': port.description,
                'version': None,
                'accessible': True,
            })
            entry['version'] = version
            responsive_count += 1
        elif status == 'permission_denied' and port.device in playdate_info:
            playdate_info[port.device]['accessible'] = False
            inaccessible_count += 1

    ports_list = sorted(playdate_info.values(), key=lambda p: p['device'])

    if responsive_count > 0:
        responsive_devices = [p['device'] for p in ports_list if p['version']]
        if not _last_crankboy_port or _last_crankboy_port not in responsive_devices:
            _last_crankboy_port = responsive_devices[0]

        return {
            'status': 'connected_running',
            'ports': ports_list,
            'message': "CrankBoy detected" if responsive_count == 1 else f"{responsive_count} CrankBoy(s) detected"
        }

    # Playdate hardware is present but we couldn't open it
    if inaccessible_count > 0:
        first_inaccessible = next(p['device'] for p in ports_list if not p['accessible'])
        return {
            'status': 'not_accessible',
            'ports': ports_list,
            'message': _build_permission_message(first_inaccessible)
        }

    # No CrankBoy response - check if Playdate hardware is connected
    if ports_list:
        return {
            'status': 'connected_not_running',
            'ports': ports_list,
            'message': "Playdate connected but CrankBoy not running - please start CrankBoy"
        }

    # No USB devices at all or none are Playdate
    return {
        'status': 'not_connected',
        'ports': [],
        'message': "Playdate not connected - please connect and unlock your device"
    }


def find_crankboy_port():
    """Find CrankBoy port, testing cached port first.

    Returns:
        tuple: (port_name, version) or (None, None) if not found
    """
    result = scan_for_crankboy()
    if result['status'] == 'connected_running':
        for p in result['ports']:
            if p.get('version'):
                return p['device'], p['version']
    return None, None


def scan_ports():
    """Scan and return only responsive CrankBoy devices.

    Returns:
        list: List of dicts with keys 'device', 'description', 'version'
              Empty list if no CrankBoy found
    """
    result = scan_for_crankboy()
    if result['status'] == 'connected_running':
        return [p for p in result['ports'] if p.get('version')]
    return []


def clear_cached_port():
    """Clear the cached CrankBoy port."""
    global _last_crankboy_port
    _last_crankboy_port = None
