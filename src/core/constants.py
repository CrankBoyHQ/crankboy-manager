"""Constants and enums for CrankBoy Manager."""

from enum import Enum

class FileStatus(Enum):
    """Status of a file in the transfer queue."""
    PENDING = "Pending"
    TRANSFERRING = "Transferring..."
    DONE = "Done"
    FAILED = "Failed"


class TransferButtonState(Enum):
    """Button text states for the transfer button."""
    START = "Start Transfer"
    STOP = "Stop Transfer"
    RESUME = "Resume Transfer"
    RETRY = "Retry Transfer"


class ArtStatus(Enum):
    """Cover art status for a file."""
    UNKNOWN = ""           # Not yet scanned for CRC32
    NO_MATCH = "No Match"  # CRC32 scanned, no entry in the cover database
    MATCH = "Match"        # Database match, art not yet downloaded
    FAILED = "Failed"      # Database match, download failed
    OK = "OK"              # Cover art downloaded successfully


ART_STATUS_LEGEND = (
    "Cover art status:\n"
    "  (blank) — CRC32 not yet scanned\n"
    "  No Match — no entry in the cover database\n"
    "  Match — matched in the database, art not yet downloaded\n"
    "  Failed — matched but download failed\n"
    "  OK — cover art downloaded"
)
