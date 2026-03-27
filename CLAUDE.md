# Pokemon Go Storage Manager — Development Guidelines

## Project Overview
ADB-based Python tool that scans Pokemon Go storage via screen capture + OCR on a Samsung Galaxy Z Fold6. Ranks Pokemon by IVs/PvP/categories and favorites the keepers for manual mass-transfer.

## Development Approach: Test-Driven Development (TDD)

**Always test first, then implement.**

1. **Before writing new features**: Write a test that captures what the feature should do. Run it, see it fail, then implement.
2. **Before fixing bugs**: Reproduce the bug with a test or manual step-through. Capture screenshots, log the actual values, compare against expected. Only then fix.
3. **Before changing OCR/detection logic**: Test on real screenshots from the phone. Save test fixtures. Verify the fix works on multiple Pokemon before integrating.
4. **Step through problematic flows manually**: Use `python -c` to execute each step, capture screenshots at each stage, inspect pixel values, OCR output, and validation results.

## Key Principles

- **No guessing** — Never auto-correct data. If OCR reads wrong, retry from the source (screen). The phone screen is the source of truth.
- **Validate everything** — CP must match IVs + HP via the formula. If it doesn't, re-read, don't fudge.
- **Screen-aware navigation** — Always screenshot and detect what screen we're on before acting. Never tap blindly.
- **No files on the phone** — Use `adb exec-out screencap -p` piped directly. Never write temp files to the device.
- **Protect Pokemon** — If we can't read a Pokemon correctly, favorite it for protection. Never risk accidental transfer.
- **Speed vs accuracy tradeoff** — Fast first read (Tesseract), accurate retry (PaddleOCR). Only retry on failures.

## Architecture

- **Scanner**: Swipes through appraisal screens, reads IVs (bars), CP, HP, name, gender
- **Multi-pass**: Uses Pokemon Go search filters (!shiny&!shadow etc.) to categorize
- **Validation**: CP formula + HP formula verify every read. IVs + HP are trusted (static card), CP is suspect (Pokemon blocks it)
- **Retry flow**: Bad read → re-read appraisal → if IVs confirmed but CP wrong → close appraisal → animate Pokemon → re-read CP from detail screen
- **Decision Engine**: Ranks by BEST_OVERALL, BEST_PVP (3 leagues), BEST_SHINY/SHADOW/DYNAMAX, size records
- **Executor**: Favorites keepers by matching species + CP + IVs + HP

## Tech Stack

- Python 3.13, PySide6 GUI
- ADB via subprocess (no files on phone)
- Tesseract (fast OCR) + PaddleOCR (accurate fallback)
- OpenCV for image processing, bar detection
- SQLite for data storage
- PvPoke gamemaster.json for base stats + PvP rankings

## Device

- Samsung Galaxy Z Fold6 (SM-F956B), cover screen 968x2376
- Foldable warning in screencap output (strip before PNG magic bytes)
- Native refresh rate, no enhanced graphics
