"""Device information and fingerprinting."""

from dataclasses import dataclass


@dataclass
class DeviceInfo:
    model: str
    serial: str
    width: int
    height: int
    density: int

    @property
    def fingerprint(self) -> str:
        """Unique identifier for calibration profile matching."""
        return f"{self.model}_{self.serial}_{self.width}x{self.height}"

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    def __str__(self) -> str:
        return f"{self.model} ({self.resolution}, density={self.density})"
