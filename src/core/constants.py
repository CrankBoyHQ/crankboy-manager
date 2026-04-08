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
