"""Constants and enums for CrankBoy Manager."""

from enum import Enum

class FileStatus(Enum):
    """Status of a file in the transfer queue."""
    PENDING = "Pending"
    TRANSFERRING = "Transferring..."
    DONE = "Done"
    FAILED = "Failed"
