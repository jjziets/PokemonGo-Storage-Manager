# Pokemon Go Storage Manager

A desktop application that connects to an Android phone via ADB, scans your entire Pokemon Go storage using screen capture + OCR, builds a local database of every Pokemon with full IV stats, applies intelligent keep/transfer rules, and favorites the keepers — so you can safely mass-transfer everything else in-game.

No modification to Pokemon Go is involved. The tool interacts with the phone exactly as a human would — via screen captures and simulated taps.

## Screenshots

### Desktop GUI

**Scan Tab** — Configure multi-pass scanning with speed tuning, scan passes (Normal, Shiny, Shadow, Dynamax, etc.), and resume support.

![Scan Tab](docs/screenshots/gui_scan_tab.png)

**Collection Browser** — Browse all 3,000+ scanned Pokemon with species sprites, IV percentages, shiny/shadow/lucky badges, and KEEP/TRANSFER decisions color-coded.

![Collection Browser](docs/screenshots/gui_collection_tab.png)

**Decision Review** — Side-by-side KEEP vs TRANSFER lists grouped by species. Move Pokemon between lists before approving. Shinies highlighted in gold, shadows in purple.

![Decision Review](docs/screenshots/gui_decisions_tab.png)

**Mass Actions** — One-click favorite by category (Shiny, Shadow, Legendary, 4-star, etc.) or favorite all keepers from the decision engine.

![Mass Actions](docs/screenshots/gui_mass_actions_tab.png)

### Phone (Samsung Galaxy Z Fold6)

**Appraisal Screen** — The tool reads CP, species name, HP, IV bars (Attack/Defense/HP), and status icons directly from the in-game appraisal overlay.

![Phone Appraisal](docs/screenshots/phone_appraisal.png)

## How It Works

```
┌──────────────┐       ADB (USB)              ┌──────────────┐
│   Desktop    │ ◄──────────────────────────► │ Android Phone│
│   (Python)   │   screencap + input tap      │ (Pokemon Go) │
│              │                               └──────────────┘
│  ┌─────────┐ │
│  │ADB Ctrl │ │  Screen capture + tap/swipe dispatch
│  ├─────────┤ │
│  │Screen   │ │  OCR (Tesseract + PaddleOCR) + pixel analysis (OpenCV)
│  │Reader   │ │
│  ├─────────┤ │
│  │Indexing │ │  State machine: appraise → read → swipe → repeat
│  │Engine   │ │
│  ├─────────┤ │
│  │SQLite   │ │  Local database, one row per Pokemon
│  │Database │ │
│  ├─────────┤ │
│  │Decision │ │  Rules engine + PvP IV rankings
│  │Engine   │ │
│  ├─────────┤ │
│  │Executor │ │  Favorites keepers by matching species + IVs + HP
│  └─────────┘ │
└──────────────┘
```

### Scanning Pipeline

1. **Connect** your phone via USB and launch Pokemon Go
2. **Multi-pass scan** uses Pokemon Go's built-in search filters (`!shiny&!shadow`, `shiny`, `shadow`, `dynamax`, etc.) to categorize Pokemon automatically
3. For each Pokemon, the tool opens the appraisal overlay and reads:
   - **Species name** — OCR from the "caught" bubble text
   - **CP** — OCR from the detail screen header
   - **IV bars** — Pixel analysis of the ATK/DEF/STA bar fill levels (0–15 each)
   - **HP** — OCR from the HP text
   - **Icons** — Pixel detection for favorite star, lucky background, gender
4. **Fingerprint validation** — IVs + HP uniquely identify the Pokemon's species and level via the CP formula. If the calculated CP matches OCR CP, the read is confirmed. If not, the tool retries with animation tricks to get an unobstructed CP read.
5. All data is stored in **SQLite** with automatic deduplication

### Decision Engine

The decision engine applies rules per species group (in priority order):

| Rule | What it keeps |
|------|--------------|
| `BEST_OVERALL` | Highest IV total, then highest CP |
| `BEST_SHINY` | Best shiny specimen per species |
| `BEST_SHADOW` | Best shadow specimen per species |
| `BEST_DYNAMAX` | Best Dynamax/Gigantamax per species |
| `BEST_PVP_LL` | Best Little League PvP candidate |
| `BEST_PVP_GL` | Best Great League PvP candidate |
| `BEST_PVP_UL` | Best Ultra League PvP candidate |
| `BEST_LIGHTEST` / `HEAVIEST` / `SHORTEST` / `TALLEST` | Size record holders |
| Safety | Never transfer the last of any species |

Everything else is marked `TRANSFER`.

### Execution

After reviewing decisions in the GUI, the executor:
1. Searches `!favorite&!shiny` (and other pass filters) in-game
2. Swipes through each Pokemon's appraisal screen
3. Matches species + IVs + HP against the keeper list
4. Taps the favorite star on matches
5. Once all keepers are favorited, you mass-transfer everything unfavorited in Pokemon Go — the game blocks transfer of favorited Pokemon as a safety net

## Features

- **Multi-pass scanning** with configurable search filters (Normal, Shiny, Shadow, Lucky, Dynamax, Gigantamax, Custom)
- **Dual OCR** — Tesseract for speed, PaddleOCR as accurate fallback
- **Fingerprint validation** — Every Pokemon verified against the CP formula before storing
- **PvP IV rankings** — Computed from PvPoke gamemaster.json for all leagues
- **Desktop GUI** (PySide6/Qt) with dark theme, live scan progress, collection browser with sorting/filtering, decision review with drag-and-drop
- **Resume support** — Fast-swipe past already-scanned Pokemon with target verification
- **Anti-detection** — Randomized delays, tap jitter, configurable micro-breaks
- **Safety first** — Unreadable Pokemon are favorited for protection, never transferred
- **Battery monitoring** — Auto-pause when battery drops below 20%
- **CSV export** for spreadsheet review
- **Dry run mode** — Test the favorite pass without actually tapping stars

## Requirements

- **Python 3.10+** (developed on 3.13)
- **Android phone** connected via USB with ADB debugging enabled
- **Pokemon Go** installed on the phone
- **Tesseract OCR** installed on the host machine
- **ADB** (Android Debug Bridge) installed

### System Dependencies

```bash
# macOS
brew install tesseract
brew install android-platform-tools

# Ubuntu/Debian
sudo apt install tesseract-ocr
sudo apt install adb
```

### Python Dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional (for improved OCR accuracy):
```bash
pip install paddleocr paddlepaddle
```

## Usage

### GUI (Recommended)

```bash
python run.py gui
```

1. Click **Connect** to establish ADB connection
2. Configure scan passes (Normal, Shiny, Shadow, etc.)
3. Adjust speed settings if needed
4. Open Pokemon Go on your phone, navigate to storage, tap any Pokemon, open its appraisal
5. Click **Multi-Pass Scan** or **Scan from Here**
6. After scanning, go to the **Decisions** tab and click **Run Decision Engine**
7. Review KEEP/TRANSFER lists, adjust as needed
8. Click **Fav Keepers (Dry)** to test, then **Fav Keepers (REAL)** to execute
9. In Pokemon Go, mass-transfer all unfavorited Pokemon

### CLI

```bash
# Test ADB connection
python run.py test-adb

# Calibrate for your device
python run.py calibrate

# Test screen reading
python run.py test-read --appraise

# Run indexing (must be on appraisal screen)
python run.py index --total 3000 --export pokemon.csv

# Run decision engine
python run.py decide --export decisions.csv
```

## Project Structure

```
pokemgr/
├── adb/
│   ├── controller.py      # ADB connection, screencap, tap, swipe
│   └── navigator.py       # In-game navigation (storage, search, appraisal)
├── calibration/
│   ├── profile.py          # Device-specific screen region calibration
│   └── regions.py          # Screen region definitions (CP, name, bars, etc.)
├── reader/
│   ├── screen.py           # ScreenReader facade
│   ├── ocr.py              # Tesseract OCR for text (CP, name, HP)
│   ├── ocr_engine.py       # PaddleOCR engine (accurate fallback)
│   ├── bars.py             # IV bar pixel analysis (ATK/DEF/STA 0-15)
│   ├── icons.py            # Icon detection (favorite, lucky, shiny)
│   └── gender.py           # Gender symbol detection
├── indexer/
│   ├── state_machine.py    # Read → swipe → read → swipe loop
│   └── multi_pass.py       # Multi-pass scan with search filters
├── data/
│   ├── database.py         # SQLite storage with dedup and sessions
│   └── models.py           # Pokemon dataclass
├── decision/
│   ├── engine.py           # Keep/transfer rule engine
│   ├── rules.py            # Individual decision rules
│   └── safety.py           # Safety checks (last of species, etc.)
├── pvp/
│   ├── calculator.py       # PvP IV ranking computation (4096 combos)
│   ├── fingerprint.py      # Species identification from IVs + HP
│   ├── cp_validator.py     # CP formula validation
│   └── rankings_db.py      # PvP rankings SQLite database
├── execution/
│   └── executor.py         # Favorite keepers via ADB
├── gui/
│   ├── app.py              # Qt application entry point
│   ├── main_window.py      # Main window with tabbed interface
│   ├── workers.py          # Background threads for scan/favorite
│   └── widgets/
│       ├── scan_control.py        # Scan configuration and progress
│       ├── collection_browser.py  # Pokemon table with sorting/filtering
│       ├── decision_review.py     # KEEP/TRANSFER review panels
│       └── mass_actions.py        # Batch favorite operations
├── sprites.py              # Pokemon sprite loading
└── config.py               # Global settings, delays, paths
```

## Device Support

Currently tested on:
- **Samsung Galaxy Z Fold6** (SM-F956B) — cover screen 968x2376

The calibration system automatically creates device-specific profiles based on model + resolution. Other Android devices should work after calibration, though the default screen regions are tuned for the Fold6 cover screen.

## Performance

Typical scan rates on Galaxy Z Fold6 (USB connection):

| Mode | Speed | Notes |
|------|-------|-------|
| Normal scan | ~30-40/min | Full validation with fingerprinting |
| Fast (Calc CP) | ~45-50/min | Skips CP OCR, uses calculated CP |
| Skip/Resume | ~600/min | Fast-swiping past already scanned |

A full 3,000 Pokemon storage scan takes approximately 1.5–2 hours.

## Safety

The tool is designed with multiple safety layers:

1. **Fingerprint validation** — Every Pokemon's stats are verified against the CP formula before storing
2. **Unreadable = protected** — If the tool can't confidently read a Pokemon, it favorites it rather than risk incorrect data
3. **Review before execute** — The decision engine output is always reviewed in the GUI before any favorites are applied
4. **Dry run mode** — Test the favorite pass without actually tapping stars
5. **Favorite-first strategy** — Keepers are favorited before any transfer happens; Pokemon Go blocks transfer of favorited Pokemon
6. **Never auto-transfer** — The tool only favorites keepers. Mass-transfer is always done manually by the user in Pokemon Go

## License

MIT
