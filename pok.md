# Pokemon Go Auto-Sorter — Engineering Spec

## Overview

A Python-based desktop tool that connects to an Android phone via ADB, indexes every Pokemon in a user's Pokemon Go storage by scanning the in-game appraisal screen, builds a local database of all Pokemon and their stats, applies configurable sorting/keeping rules, and then executes batch actions (favorite keepers, transfer the rest).

No modification to Pokemon Go is involved. The tool interacts with the phone exactly as a human would — via screen captures and simulated taps.

---

## Architecture

```
┌──────────────┐       ADB (USB or Wi-Fi)       ┌──────────────┐
│   PC / Host  │ ◄──────────────────────────────► │ Android Phone│
│              │   screencap, input tap/swipe     │ (Pokemon Go) │
│  Python App  │                                  └──────────────┘
│  ┌────────┐  │
│  │ADB Ctrl│  │  ← screen capture + tap/swipe dispatch
│  ├────────┤  │
│  │Screen  │  │  ← OCR (Tesseract) + pixel analysis (OpenCV)
│  │Reader  │  │
│  ├────────┤  │
│  │State   │  │  ← drives the tap sequence through the UI
│  │Machine │  │
│  ├────────┤  │
│  │Data    │  │  ← SQLite or CSV, one row per Pokemon
│  │Store   │  │
│  ├────────┤  │
│  │Decision│  │  ← rules engine + PvP IV tables
│  │Engine  │  │
│  └────────┘  │
└──────────────┘
```

---

## Phase 1 — Indexing

### Objective

Tap into every Pokemon in storage, open the appraisal screen, read all stats, and store them locally. The user pre-sorts their storage by Pokedex number in-game before starting.

### ADB Setup

- Connect via USB (`adb devices`) or Wi-Fi (`adb tcpip 5555`, then `adb connect <phone-ip>:5555`)
- Validate connection before starting

### Calibration System

#### Device Detection and Profile Management

On ADB connection, the tool automatically identifies the device:

- Run `adb shell getprop ro.product.model` → device model (e.g. "SM-F946B" for Galaxy Fold5)
- Run `adb shell getprop ro.serialno` → unique serial number
- Run `adb shell wm size` → screen resolution
- Run `adb shell wm density` → display density

This creates a **device fingerprint** (`model + serial + resolution`). The tool checks if a calibration profile already exists for this fingerprint:

- **Profile found** → load it, show a summary ("Using saved calibration for Galaxy Fold5 — 2176x1812. Last used 3 days ago. Recalibrate?"), and skip straight to indexing unless the user wants to redo it.
- **Profile not found** → launch the guided calibration wizard.

Calibration profiles are stored in a `calibrations/` directory, one JSON file per device:

```
calibrations/
  SM-F946B_R5ABC123_2176x1812.json   ← Fold5 (inner screen)
  SM-F946B_R5ABC123_2640x1080.json   ← Fold5 (cover screen, different res)
  SM-F741B_R5XYZ789_2640x1080.json   ← Flip5
  SM-S921B_R5DEF456_3120x1440.json   ← S24
```

Note: foldables may present different resolutions depending on which screen is active. The resolution is part of the fingerprint, so inner screen and cover screen get separate profiles automatically.

#### Guided Calibration Wizard

The wizard walks the user through each step in the GUI. Each step shows the live phone screenshot with instructions overlaid.

**Step 1 — Open Pokemon Go**
- Instruction: "Open Pokemon Go and go to your Pokemon storage. Tap any Pokemon to open its detail screen."
- The tool polls via screenshot until it detects the detail screen layout (dark background, CP text at top). Or the user can just click "Ready" in the GUI.

**Step 2 — Pokemon Detail Screen**
- Tool takes a screenshot and displays it in the GUI
- Auto-detection attempt: use edge detection and text recognition to propose bounding boxes for:
  - Pokemon name region
  - CP value region
  - Shiny icon region
  - Shadow icon region
  - Favorite star region
  - Appraise button region
- The GUI overlays these as draggable/resizable colored rectangles on the screenshot
- User adjusts any that are wrong, then clicks "Confirm"

**Step 3 — Appraisal Screen**
- Instruction: "Tap the Appraise button on your phone."
- Tool detects the appraisal screen has loaded (screenshot diffing — wait until stable)
- Auto-detect bounding boxes for:
  - ATK bar region
  - DEF bar region
  - STA bar region
  - Close/dismiss appraisal tap target
- User adjusts and confirms

**Step 4 — Navigation**
- Instruction: "Swipe left on your phone to go to the next Pokemon."
- Tool captures the swipe gesture coordinates (start and end point)
- Or: tool sets default swipe coordinates (center-right to center-left) and asks user to confirm it worked
- Also captures the back button / X button location for returning to the list view

**Step 5 — Validation**
- Tool reads the current Pokemon's stats using the calibrated regions
- Displays the result in the GUI:
  ```
  Detected: Machamp | CP 2847 | ATK 15 | DEF 14 | STA 12 | Not Shiny | Not Shadow | Favorited
  ```
- User confirms whether this is correct
- If wrong, user can go back to the relevant step and adjust
- If correct, tool asks user to swipe to 2-3 more Pokemon and validates each one to build confidence
- After 3 successful consecutive reads → calibration is complete

**Step 6 — Save**
- Profile saved to `calibrations/<fingerprint>.json`
- Contents include:
  - Device model, serial, resolution, density
  - All bounding box coordinates (as percentages of screen size for portability reference, plus absolute pixels)
  - Tap targets (appraise button, close appraisal, favorite star)
  - Swipe gesture coordinates and duration
  - Timestamp of calibration
  - Validation results (the Pokemon it successfully read during step 5)

#### Recalibration

Accessible from the GUI at any time via a "Recalibrate" button. Also triggered automatically if:

- The tool detects repeated OCR failures during indexing (e.g. 5 consecutive unreadable screenshots)
- Pokemon Go updates and the UI layout shifts (detected by validation failures)

In these cases the tool pauses, saves progress, and prompts: "Screen reading is failing — Pokemon Go may have updated its UI. Would you like to recalibrate?"

### Tap Sequence Per Pokemon

```
State: POKEMON_DETAIL_SCREEN
  1. Screenshot → read species name, CP, shiny icon, shadow aura, favorite state
  2. Tap [Appraise button]
  3. Wait ~2s for appraisal animation
State: APPRAISAL_SCREEN
  4. Screenshot → read ATK / DEF / STA bars (0–15 each)
  5. Tap to close appraisal
  6. Wait ~0.5s
  7. Swipe left to next Pokemon
  8. Wait ~0.5s for transition
  9. Screenshot → check if species name changed
     - If same species or new species: continue loop
     - If back to first Pokemon in storage (wrap-around detected): indexing complete
```

### Screen Reading

**Species name / CP**: Use Tesseract OCR on the calibrated bounding box. The text is large, high-contrast, fixed-position — should be reliable. Sanitize output (trim whitespace, normalize casing).

**Appraisal stat bars**: Each bar represents a value from 0 to 15. Options for reading:

- **Pixel sampling**: The bar fill is a distinct color against the background. Sample pixel color at 16 evenly spaced points along each bar. Count how many are "filled" → that's the stat value.
- **Template matching**: Pre-capture bar images for values 0–15 during calibration, then match.

Pixel sampling is recommended — simpler, faster, and resolution-independent once calibrated.

**Shiny detection**: Check for the shiny sparkle icon (3 stars) near the Pokemon's name. Pixel check at the known region — if the icon is present, the distinct color/shape will differ from the default.

**Shadow detection**: Shadow Pokemon have a purple aura and "purified" have a different indicator. Check the known region for the purple flame icon. Use color thresholding in the expected region.

**Favorite state**: The star icon is either filled (gold) or outline (gray). Sample the center pixel of the star region — gold = favorited, gray = not.

### Timing and Human Emulation

All wait durations should use a base time + random jitter:

```python
def human_delay(base_seconds: float, jitter: float = 0.3):
    time.sleep(base_seconds + random.uniform(0, jitter))
```

Tap coordinates should also be jittered slightly (±5–10px) around the calibrated center point.

### Data Model

Each indexed Pokemon is stored as a row:

| Field       | Type    | Source            |
|-------------|---------|-------------------|
| index       | int     | Auto-increment    |
| species     | string  | OCR               |
| cp          | int     | OCR               |
| atk         | int     | Bar pixel read    |
| def_        | int     | Bar pixel read    |
| sta         | int     | Bar pixel read    |
| iv_total    | int     | Computed (atk+def+sta) |
| iv_pct      | float   | Computed (total/45)    |
| shiny       | bool    | Icon detection    |
| shadow      | bool    | Icon detection    |
| favorited   | bool    | Icon detection    |
| position    | int     | Sequential order in list |

Store in **SQLite** for easy querying. Also support **CSV export** so the user can review everything in a spreadsheet before committing to any actions.

### Wrap-Around Detection

When scrolling through Pokemon sorted by Pokedex number, detect indexing completion by one of:

- Reaching the same species + CP + IV combo as the first entry (likely unique enough)
- Tracking total count vs known storage size (if the user inputs their total)
- Detecting a Pokemon that has already been indexed (match on species + CP + IVs)

---

## Phase 2 — Decision Engine

### Objective

Process the indexed database and mark each Pokemon as KEEP or TRANSFER, with a reason tag.

### Rules (applied per species group)

#### Rule 1: Best Overall

Sort all specimens of the same species by:
1. `iv_total` descending
2. `shiny` descending (shiny wins ties)
3. `cp` descending (higher CP wins ties)

**Keep the #1 result.** Tag reason: `BEST_OVERALL`.

#### Rule 2: Best Shiny

If any shinies exist for that species:
- Among shinies only, sort by `iv_total` desc → `cp` desc
- Keep the #1 shiny. Tag reason: `BEST_SHINY`.
- (Skip if the best shiny is already kept as BEST_OVERALL.)

#### Rule 3: Best Shadow

If any shadows exist for that species:
- Among shadows only, filter to **3 stars or above (iv_total >= 37, i.e. roughly 82%+)**
- If any pass the filter, sort by `iv_total` desc → `cp` desc
- Keep the #1 qualifying shadow. Tag reason: `BEST_SHADOW`.
- If no shadow meets the 3-star threshold, keep none.

#### Rule 4: Best PvP Candidate

For species that are relevant in Great League (CP cap 1500) or Ultra League (CP cap 2500):

- Look up the ideal PvP IV spread from a bundled ranking table (source: PvPoke or equivalent community data)
- For each specimen, calculate a **PvP rank** — how close its spread is to the ideal for that league
- Keep the #1 PvP-ranked specimen. Tag reason: `BEST_PVP_GL` or `BEST_PVP_UL`.

**PvP IV ranking data**: Rather than scraping pre-built ranking tables, compute them from base stats using the Pokemon Go CP and stat formulas. This is more maintainable and always accurate. See the "PvP Data Pipeline" section below for full details.

#### Everything Else

Any Pokemon not tagged by the above rules → mark as `TRANSFER`.

#### Safety Rules

- **Never transfer a shiny** unless there's a better shiny of the same species (user can override)
- **Never transfer a favorited Pokemon** without explicit user confirmation
- **Never transfer the last remaining specimen of any species** (preserve Pokedex completion)

### Output

Generate a **decision report** (CSV or terminal table) showing:

```
Species     | ATK | DEF | STA | IV%  | CP   | Shiny | Shadow | Decision     | Reason
Machamp     | 15  | 14  | 12  | 91%  | 2847 | No    | No     | KEEP         | BEST_OVERALL
Machamp     | 1   | 15  | 14  | 67%  | 1498 | No    | No     | KEEP         | BEST_PVP_GL
Machamp     | 8   | 11  | 9   | 62%  | 2201 | No    | No     | TRANSFER     |
Machamp     | 13  | 13  | 13  | 87%  | 1205 | Yes   | No     | KEEP         | BEST_SHINY
```

**The user must review and approve this report before Phase 3 begins.**

---

## Phase 3 — Execution

### Objective

Favorite all keepers, then batch-transfer everything else.

### Pass 1: Favorite Keepers

For each Pokemon marked KEEP that is not already favorited:
1. Navigate to that Pokemon (by scrolling to its known position)
2. Tap the favorite star
3. Verify the star changed to filled (screenshot + pixel check)
4. Move to next

### Pass 2: Batch Transfer

1. Enter selection mode (long-press any non-favorited Pokemon)
2. Tap to select all TRANSFER-marked Pokemon visible on screen
3. Scroll and continue selecting
4. Hit Transfer → Confirm
5. Pokemon Go will block transfer of any favorited Pokemon, providing a safety net

### Navigation

Navigating to specific Pokemon by position requires scrolling to the right place in the list. Options:

- **Sequential**: Start from the top, scroll through, counting positions. Slow but reliable.
- **Search by name**: Use Pokemon Go's in-game search bar to filter to a specific species (type the name via `adb shell input text "Machamp"`). This narrows the list and makes finding specific Pokemon faster.

The **search-by-name approach** is strongly recommended for Phase 3 — it groups each species so you can favorite/transfer within a small filtered set rather than scrolling through 5,000 entries.

---

## Desktop GUI — Pokemon Manager

### Objective

A visual desktop application that serves as the primary interface for reviewing, sorting, approving, and managing the Pokemon collection. The CLI is only used for initial setup (calibration, ADB connection). Everything else happens through the GUI.

### Main View — Collection Browser

Displays the full indexed collection in a table/grid view. Each Pokemon shows:

- **Thumbnail** (screenshot captured during indexing, cropped to the Pokemon image)
- Species name
- CP
- ATK / DEF / STA (with visual bar indicators)
- IV% (color-coded: green 90%+, yellow 70-89%, red below)
- Shiny badge (sparkle icon if shiny)
- Shadow badge (purple flame if shadow)
- Favorite state (star icon)
- Decision tag (BEST_OVERALL, BEST_PVP_GL, BEST_SHINY, BEST_SHADOW, TRANSFER — color-coded)

### Sorting and Filtering

Sortable columns (click header to sort, click again to reverse):

- CP (high to low / low to high)
- IV% (high to low / low to high)
- PvP Rank (best to worst)
- Species (alphabetical / Pokedex number)
- ATK / DEF / STA individually

Filter bar with toggles and dropdowns:

- **Type filter**: show only specific types (Water, Fire, etc.)
- **Shiny only** toggle
- **Shadow only** toggle
- **Legendary only** toggle
- **Decision filter**: show only KEEP, show only TRANSFER, show all
- **Species search**: text input to filter by name
- **IV range slider**: min-max IV% filter
- **Star rating**: 0-star, 1-star, 2-star, 3-star, 4-star filter
- **League filter**: show only Great League relevant, Ultra League relevant, or all

### Decision Review and Approval Workflow

After the decision engine runs, the GUI presents a review screen:

**Keep List** (left panel):
- Grouped by species
- Each entry shows the Pokemon, its stats, and *why* it was kept (BEST_OVERALL, BEST_PVP, etc.)
- User can click any keeper and demote it to TRANSFER if they disagree
- User can also manually promote any Pokemon from the transfer list to KEEP

**Transfer List** (right panel):
- All Pokemon marked for transfer
- Grouped by species for easy scanning
- Checkbox selection on each entry
- "Select All" / "Deselect All" buttons
- Bulk selection tools: "Select all of species X", "Select all below X IV%"

**Approval flow**:
1. User reviews both lists
2. User can drag/move Pokemon between KEEP and TRANSFER
3. User clicks "Approve Transfer List"
4. Confirmation dialog shows count: "You are about to transfer 3,847 Pokemon. This cannot be undone. Proceed?"
5. On confirm, execution phase begins

### Manual Management Mode

Beyond the bulk sort-and-transfer workflow, the GUI provides an ongoing management interface:

- **Select any Pokemon** in the browser → action buttons appear:
  - Favorite / Unfavorite (dispatches tap via ADB)
  - Transfer (with confirmation)
  - Rename (types name via ADB `input text`)
  - Power Up (tap sequence via ADB — with confirmation for stardust spend)
- **Search and find on phone**: click a Pokemon in the GUI → the tool uses Pokemon Go's in-game search to navigate to that specific Pokemon on the phone screen. Uses `adb shell input text "<species_name>"` in the search bar, then scrolls to match by CP/IVs.
- **Refresh**: re-scan a single Pokemon to update its data after changes (e.g., after powering up)

This effectively turns the desktop into a remote control for Pokemon storage management, which is much faster than tapping through the phone UI.

### Execution Progress View

When the tool is running actions (favoriting, transferring), the GUI shows:

- Current action ("Favoriting Machamp #2 — 15/14/12")
- Progress bar (347 / 3847 transfers complete)
- Live phone screenshot feed (updated every action, so the user can watch what's happening)
- Pause / Resume button
- Abort button (stops immediately, all completed actions are already persisted)
- Error log (if an OCR verification fails or a tap doesn't register, log it and skip)

### Tech Choice for GUI

**Recommended: Electron or Tauri with a React frontend**, or **PyQt / PySide6** if staying pure Python.

If the developer is more comfortable in web tech, a local web app (FastAPI backend + React frontend) also works — the backend handles ADB and data, the frontend is the visual layer, communication over localhost WebSocket for real-time screenshot streaming.

The simpler Python-native option is **PySide6 (Qt)**. It handles tables, image display, sorting, and filtering well out of the box without needing a separate frontend build chain.

Either approach works. Pick based on developer skill set.

---

## Tech Stack

| Component        | Tool / Library                          |
|------------------|-----------------------------------------|
| Language         | Python 3.10+                            |
| ADB interface    | `subprocess` calls or `pure-python-adb` |
| OCR              | Tesseract via `pytesseract`             |
| Image processing | OpenCV (`cv2`)                          |
| Data storage     | SQLite via `sqlite3` (stdlib)           |
| PvP IV tables    | Computed from PvPoke gamemaster.json (MIT licensed, open source) |
| Config           | `calibration.json` per device           |

---

## PvP Data Pipeline

### Why Compute Instead of Scrape

PvP IV rankings for every species in every league are fully deterministic — they're derived from the Pokemon's base stats and the CP formula. There's no opinion or weighting involved. Rank 1 is always the IV spread that produces the highest stat product while staying under the league's CP cap. This means we don't need to scrape rankings from any website. We compute them ourselves from a single data source.

### Data Source: PvPoke Game Master

PvPoke is open source (MIT license) and maintains a `gamemaster.json` file containing every Pokemon's base stats, types, moves, and evolution data. This is the single source of truth.

```
https://raw.githubusercontent.com/pvpoke/pvpoke/master/src/data/gamemaster.json
```

The file contains entries like:

```json
{
  "speciesId": "swampert",
  "speciesName": "Swampert",
  "baseStats": { "atk": 208, "def": 175, "hp": 225 },
  "types": ["water", "ground"],
  "fastMoves": ["water_gun", "mud_shot"],
  "chargedMoves": ["earthquake", "hydro_cannon", "sludge_wave", "surf"],
  ...
}
```

There is also a community-maintained API at `pogoapi.net` that provides similar data in a REST format, useful as a fallback or cross-reference.

### Computing PvP IV Rankings

For each species and each league (Great 1500, Ultra 2500), compute rankings as follows:

1. Iterate all 4,096 IV combinations (ATK 0–15, DEF 0–15, STA 0–15)
2. For each combination, find the highest half-level (1 to 50) where CP stays at or below the league cap
3. At that level, compute the **stat product**: `(base_atk + iv_atk) * cpm * (base_def + iv_def) * cpm * floor((base_sta + iv_sta) * cpm)`
4. Rank all 4,096 combinations by stat product descending
5. The #1 ranked spread is the ideal PvP IV for that species in that league

The CP formula and CPM (CP Multiplier) table per level are well documented and stable. Store the CPM table as a static lookup.

### Build Step

Add a `build_pvp_tables.py` script that:

1. Downloads `gamemaster.json` from the PvPoke GitHub repo (or uses a local cached copy)
2. For every species, computes the full 4,096-entry IV ranking for Great League and Ultra League
3. Stores the top 100 ranked spreads per species per league (that's more than enough for comparison)
4. Saves to `pvp_rankings.sqlite` or a set of JSON files

This script should be run periodically (e.g. after a Pokemon Go game update adds new species or rebalances stats). It takes a few minutes to compute across all species but only needs to run once per update.

### Usage in Decision Engine

When evaluating a scanned Pokemon's PvP potential:

```python
# Look up where this specimen's IVs fall in the precomputed ranking
rank = pvp_table.get_rank(species="swampert", league="great", atk=0, def_=15, sta=15)
# rank = 1 means perfect PvP IVs, rank = 4096 means worst possible
```

### Species Relevance Filtering

Not every species is worth evaluating for PvP. A Caterpie with rank 1 PvP IVs is still useless. Use PvPoke's overall rankings (also available in the repo under `src/data/overall/`) to filter to species that are actually competitive. Only compute and evaluate PvP IVs for species that appear in the top ~200 of their respective league rankings.

### Update Cadence

The gamemaster data changes when Niantic releases new Pokemon, adds moves, or rebalances stats. This happens roughly every 1–2 months during a "season" change. The tool should:

- Cache the gamemaster locally
- On startup, check the GitHub file's last commit date against the local cache
- If newer, prompt: "PvPoke data has been updated. Rebuild PvP rankings? (takes ~2 min)"
- Rebuild and re-evaluate any already-indexed Pokemon against the new tables

---

## Suggestions and Edge Cases

### Things the spec currently doesn't handle well

**Evolved forms and candy decisions**: The tool indexes what you have, but doesn't consider evolution potential. A low-CP Mudkip with 0/15/15 is potentially a perfect PvP Swampert — but only if evolved. The decision engine should cross-reference evolution chains (available in the gamemaster) and flag unevolved Pokemon that would become top PvP candidates after evolution. Tag these as `KEEP — EVOLVE_FOR_PVP`.

**Mega and Primal eligibility**: Some species have Mega or Primal evolutions which make even moderate-IV specimens valuable for Mega raids. Consider a simple flag: if a species has a Mega form and the specimen is the best available for that species, mark it as a Mega candidate.

**Costume and event Pokemon**: Some Pokemon have hats, costumes, or event tags and can't be evolved. These are cosmetic collectibles. The tool should detect them (they usually have modified names or icons) and apply a separate rule — keep all costume Pokemon by default, or at least don't auto-transfer them without explicit approval.

**Lucky Pokemon**: Lucky Pokemon (from trades) have a gold background and have guaranteed minimum 12/12/12 IVs. They also cost half stardust to power up. The indexer should detect the lucky indicator, and the decision engine should factor in the stardust discount when ranking — a lucky 12/12/12 may be more practical to power up than a non-lucky 15/15/15.

**Tag and naming preservation**: If a user has already manually renamed or tagged Pokemon (common in the community — e.g. naming them with IV shorthand like "15-14-12"), the tool should read and preserve those names. Don't overwrite user-applied names during any rename operations.

### Resilience improvements

**Screenshot diffing for state detection**: Rather than relying on fixed delays to know when an animation has finished, diff consecutive screenshots. When the pixel difference drops below a threshold, the screen has settled and is ready to read. This handles variable animation speed across different phone hardware.

**Confidence scoring on OCR reads**: For each stat read, compute a confidence score. If any single Pokemon's read falls below threshold (e.g. one of the IV bars reads ambiguously), flag it for manual review rather than guessing. Store the raw screenshot so the user can check it later.

**Duplicate detection during indexing**: If the tool accidentally reads the same Pokemon twice (e.g. a swipe didn't register), detect duplicates by matching species + CP + all three IVs and deduplicate.

**Battery and thermal management**: A 12-hour indexing run will heat the phone and drain the battery. The tool should check battery level via `adb shell dumpsys battery` and pause if it drops below 20%, prompting the user to plug in. Optionally, add configurable break intervals (e.g. pause 5 minutes every hour) to let the phone cool down.

### Quality of life

**Progress estimates**: Based on the first 50 Pokemon processed, calculate average time per Pokemon and display a remaining time estimate. Update this estimate as it goes — some species screens load faster than others.

**Dry run mode**: A mode that goes through the entire indexing and decision pipeline but never executes any transfers. Outputs the full report so the user can verify everything looks correct before doing a real run.

**Undo log**: Before any transfer pass, dump the complete list of Pokemon being transferred (species, IVs, CP) to a timestamped log file. If something goes wrong, at least the user has a record of exactly what was lost, which is useful for contacting Niantic support.

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| OCR misreads a stat value | Log every screenshot during indexing. Add a verification pass that flags suspicious values (e.g. stat > 15, empty reads). User can manually correct from the saved screenshots. |
| Appraisal animation timing varies | Use screenshot diffing to detect when the appraisal screen has fully loaded rather than relying on fixed delays. |
| Phone disconnects mid-run | Persist all state to SQLite after every Pokemon. The tool can resume from where it left off. |
| Accidental transfer of valuable Pokemon | Favorite-first strategy ensures favorited Pokemon cannot be transferred. User reviews decision report before execution. |
| Niantic detection | Randomized delays (base + jitter) on all interactions. Randomized tap coordinate offsets (±5-10px). Recommend running in reasonable time windows, not 24/7. |

---

## Development Order

1. **ADB controller module** — screencap, tap, swipe with jitter
2. **Calibration flow** — guided setup to capture screen regions
3. **Screen reader** — OCR for name/CP, pixel analysis for IV bars, icon detection for shiny/shadow/favorite
4. **Indexing state machine** — full loop: open → appraise → read → close → next
5. **Data store** — SQLite schema, insert/query, CSV export
6. **Decision engine** — rules implementation + PvP IV table lookup
7. **Desktop GUI — Collection browser** — table view with sorting/filtering, thumbnail display
8. **GUI — Review and approval workflow** — keep/transfer lists, drag to reclassify, bulk selection, approve and confirm
9. **Execution module** — favorite pass + batch transfer pass with progress view and live screenshot feed
10. **GUI — Manual management mode** — single Pokemon actions (favorite, transfer, rename, search-on-phone)
11. **CLI fallback** — headless commands for `calibrate`, `index`, `decide`, `execute` for advanced users or scripting

Each phase is independently testable. The developer can validate indexing accuracy on a small sample before running a full scan. The GUI can be developed in parallel once the data store and decision engine are working — it just reads from SQLite.
