#!/usr/bin/env python3
"""Build PvP IV ranking tables from PvPoke gamemaster data.

Run this periodically (after game updates) to recompute rankings.
Takes ~2-5 minutes for all species.

Usage:
    python scripts/build_pvp_tables.py [--force-download]
"""

import sys
import time
import argparse
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pokemgr.pvp.gamemaster import download_gamemaster, parse_species
from pokemgr.pvp.calculator import rank_ivs_for_league
from pokemgr.pvp.rankings_db import PvPRankingsDB
from pokemgr.config import ensure_dirs

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

LEAGUES = {
    "little": 500,
    "great": 1500,
    "ultra": 2500,
    # Master League: no CP cap, 15/15/15 is always rank 1 = same as BEST_OVERALL
}


def main():
    parser = argparse.ArgumentParser(description="Build PvP IV ranking tables")
    parser.add_argument("--force-download", action="store_true",
                        help="Force re-download of gamemaster.json")
    parser.add_argument("--species", type=str, default=None,
                        help="Only compute for a specific species (for testing)")
    parser.add_argument("--top-n", type=int, default=100,
                        help="Store top N rankings per species per league")
    args = parser.parse_args()

    ensure_dirs()

    # Download and parse gamemaster
    gamemaster = download_gamemaster(force=args.force_download)
    species_map = parse_species(gamemaster)
    log.info("Found %d species in gamemaster", len(species_map))

    # Open rankings database
    pvp_db = PvPRankingsDB()

    # Filter to specific species if requested
    if args.species:
        target = args.species.lower()
        species_map = {k: v for k, v in species_map.items() if target in k}
        log.info("Filtered to %d matching species", len(species_map))

    start = time.time()
    total = len(species_map)
    computed = 0

    for i, (species_id, data) in enumerate(species_map.items()):
        base_atk = data["base_atk"]
        base_def = data["base_def"]
        base_sta = data["base_sta"]

        # Skip species with very low base stats (not competitive)
        if base_atk + base_def + base_sta < 300:
            continue

        for league_name, cp_cap in LEAGUES.items():
            rankings = rank_ivs_for_league(base_atk, base_def, base_sta, cp_cap)
            if rankings:
                pvp_db.store_rankings(species_id, league_name, rankings, args.top_n)

        computed += 1
        if computed % 50 == 0:
            elapsed = time.time() - start
            rate = computed / elapsed
            remaining = (total - computed) / rate if rate > 0 else 0
            log.info("Progress: %d/%d species (%.1f/s, ~%.0fs remaining)",
                     computed, total, rate, remaining)

    elapsed = time.time() - start
    pvp_db.set_build_info("last_build_time", f"{elapsed:.1f}s")
    pvp_db.set_build_info("species_count", str(computed))
    pvp_db.close()

    log.info("Done! Computed rankings for %d species in %.1fs", computed, elapsed)


if __name__ == "__main__":
    main()
