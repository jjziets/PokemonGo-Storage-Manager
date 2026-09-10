# TRACEWEAVER: file-role=runtime-cp-policy; req=REQ-SCAN-002; trace=TRACE-SCAN-002; ver=VER-SCAN-001
"""Global configuration, constants, and utilities."""

import os
import time
import random
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).parent.parent

# ADB
ADB_PATH = os.environ.get("ADB_PATH", "/opt/homebrew/bin/adb")

# Fixed app canvas shared by Android phones and tablets. DPI is part of the
# layout: identical pixels with a different density can trigger tablet UI.
STANDARD_STREAM_WIDTH = 968
STANDARD_STREAM_HEIGHT = 2376
STANDARD_STREAM_DENSITY = 420

# Directories
CALIBRATIONS_DIR = PROJECT_ROOT / "calibrations"
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = PROJECT_ROOT / "cache"
LOGS_DIR = PROJECT_ROOT / "logs"
SCREENSHOTS_DIR = CACHE_DIR / "indexing"

# Database
DB_PATH = DATA_DIR / "pokemon.db"
PVP_DB_PATH = DATA_DIR / "pvp_rankings.db"

# ── Speed / Anti-detection ────────────────────────────────────────────
# Jitter on tap/swipe positions (always on for safety)
DEFAULT_TAP_JITTER = 8         # pixels
DEFAULT_SWIPE_JITTER = 10      # pixels

# Swipe speed
DEFAULT_SWIPE_DURATION_MS = 120  # fast flick
SWIPE_DURATION_JITTER_MS = 30    # minimal variation

# Delays (seconds) — tuned for native refresh rate + no enhanced graphics
DELAY_AFTER_APPRAISE_TAP = (0.0, 0.0)   # only used when first opening appraisal
DELAY_AFTER_CLOSE_APPRAISAL = (0.05, 0.05)
DELAY_AFTER_SWIPE = (0.0, 0.0)          # zero — screencap itself takes ~0.9s which is enough
STABLE_FRAME_INTERVAL = (0.15, 0.10)   # Capture-start minimum plus random jitter; transport counts.

# Anti-detection (optional — can disable via GUI)
ANTI_DETECTION_ENABLED = True
MICRO_BREAK_INTERVAL = (50, 120)    # every 50-120 Pokemon
MICRO_BREAK_DURATION = (1.0, 3.0)   # 1-3 second pause

# Bar detection
BAR_WAIT_MAX = 0.0  # max seconds to wait for bars to appear

# CP mode
# Use species/form + HP + IV evidence first when it leaves one exact CP.
# Ambiguous results use visible CP, then optional animation recovery.
USE_CALCULATED_CP = True
USE_CP_ANIMATION_RECOVERY = True
CAPTURE_SIZE_TAGS = False  # Read visible size labels with extra OCR when enabled.


# TRACEWEAVER: entrypoint=calculated_cp_recovery_enabled; req=REQ-SCAN-002; trace=TRACE-SCAN-002; ver=VER-SCAN-001
def calculated_cp_recovery_enabled() -> bool:
    """Return the current exact-CP recovery policy for a new scan worker."""
    return bool(USE_CALCULATED_CP)


def cp_animation_recovery_enabled() -> bool:
    """Return whether a new scan may use model gestures and CP previews."""
    return bool(USE_CP_ANIMATION_RECOVERY)

# ── Battery ──────────────────────────────────────────────────────────
MIN_BATTERY_LEVEL = 20
BATTERY_CHECK_INTERVAL = 100

# ── OCR ──────────────────────────────────────────────────────────────
OCR_CONFIDENCE_THRESHOLD = 0.6

# ── Screenshot diffing ───────────────────────────────────────────────
SCREEN_STABLE_THRESHOLD = 8.0
SCREEN_STABLE_MAX_WAIT = 3.0
SCREEN_STABLE_POLL = 0.15

# ── Performance ──────────────────────────────────────────────────────
SAVE_SCREENSHOTS = False
DB_BATCH_SIZE = 10


def human_delay(base_seconds: float, jitter: float = 0.3):
    """Sleep for a human-like duration with random jitter."""
    time.sleep(base_seconds + random.uniform(0, jitter))


def jitter_coord(value: int, amount: int = DEFAULT_TAP_JITTER) -> int:
    """Add random jitter to a coordinate."""
    return value + random.randint(-amount, amount)


def maybe_micro_break(count: int, next_break_at: int) -> int:
    """Take a random micro-break if count has reached the threshold.

    Returns the next break threshold. Skipped if ANTI_DETECTION_ENABLED is False.
    """
    if not ANTI_DETECTION_ENABLED:
        return next_break_at + 999999  # effectively disabled

    if count >= next_break_at:
        pause = random.uniform(*MICRO_BREAK_DURATION)
        import logging
        logging.getLogger(__name__).info(
            "Micro-break: pausing %.1fs after %d Pokemon (anti-detection)", pause, count
        )
        time.sleep(pause)
        return count + random.randint(*MICRO_BREAK_INTERVAL)
    return next_break_at


def ensure_dirs():
    """Create all runtime directories."""
    for d in [CALIBRATIONS_DIR, DATA_DIR, CACHE_DIR, LOGS_DIR, SCREENSHOTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
