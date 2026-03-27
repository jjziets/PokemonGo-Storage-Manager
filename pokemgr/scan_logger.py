"""Scan logger — creates a detailed log file for each scan session.

Log files go to logs/scan_YYYYMMDD_HHMMSS.log with full debug verbosity.
"""

import logging
import time
from pathlib import Path
from .config import LOGS_DIR


_scan_logger: logging.Logger | None = None
_file_handler: logging.FileHandler | None = None


def start_scan_log() -> str:
    """Start a new scan log file. Returns the log file path."""
    global _scan_logger, _file_handler

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"scan_{timestamp}.log"

    # Create or get the scan logger
    _scan_logger = logging.getLogger("pokemgr")

    # Remove old file handler if any
    if _file_handler:
        _scan_logger.removeHandler(_file_handler)

    # Add file handler with DEBUG level
    _file_handler = logging.FileHandler(str(log_path))
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    _scan_logger.addHandler(_file_handler)

    # Make sure the logger itself passes DEBUG
    _scan_logger.setLevel(logging.DEBUG)

    _scan_logger.info("=== Scan log started: %s ===", log_path)
    return str(log_path)


def stop_scan_log():
    """Close the scan log file."""
    global _file_handler, _scan_logger

    if _scan_logger and _file_handler:
        _scan_logger.info("=== Scan log ended ===")
        _scan_logger.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None
