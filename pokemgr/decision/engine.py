"""Decision engine — applies keep/transfer rules to the indexed collection.

Rules per species (in priority order):
  1. BEST_OVERALL — best IV total, then CP
  2. BEST_SHINY — best among shinies (rest of shinies get transferred)
  3. BEST_SHADOW — best among shadows (rest transferred)
  4. BEST_DYNAMAX — best among Dynamax/Gmax (rest transferred)
  5. BEST_PVP_GL / BEST_PVP_UL — best PvP candidate per league
  6. BEST_LIGHTEST / BEST_HEAVIEST / BEST_SHORTEST / BEST_TALLEST — size records
  7. LAST_OF_SPECIES safety — never transfer the last one

Everything else → TRANSFER
"""

import logging

from ..data.database import PokemonDatabase
from ..data.models import Pokemon
from ..pvp.rankings_db import PvPRankingsDB
from . import rules
from .safety import check_safety

log = logging.getLogger(__name__)


ALL_RULES = [
    "BEST_OVERALL",
    "BEST_SHINY",
    "BEST_SHADOW",
    "BEST_DYNAMAX",
    "BEST_PVP_LL",
    "BEST_PVP_GL",
    "BEST_PVP_UL",
    "BEST_LIGHTEST",
    "BEST_HEAVIEST",
    "BEST_SHORTEST",
    "BEST_TALLEST",
]


class DecisionEngine:
    """Processes all Pokemon and assigns KEEP or TRANSFER decisions."""

    def __init__(self, db: PokemonDatabase, pvp_db: PvPRankingsDB | None = None,
                 enabled_rules: list[str] | None = None):
        self.db = db
        self.pvp_db = pvp_db
        self.enabled_rules = set(enabled_rules) if enabled_rules else set(ALL_RULES)

    def run(self, session_id: str | None = None):
        """Run the decision engine on all Pokemon in the session."""
        log.info("Running decision engine...")
        self.db.clear_decisions(session_id)

        species_list = self.db.get_species_list(session_id)
        total_keep = 0
        total_transfer = 0

        for species in species_list:
            pokemon = self.db.get_by_species(species, session_id)
            if not pokemon:
                continue

            keep_count, transfer_count = self._decide_for_species(species, pokemon)
            total_keep += keep_count
            total_transfer += transfer_count

        log.info("Decisions complete: %d KEEP, %d TRANSFER",
                 total_keep, total_transfer)
        return {"keep": total_keep, "transfer": total_transfer}

    def _decide_for_species(self, species: str,
                            pokemon: list[Pokemon]) -> tuple[int, int]:
        """Apply all rules to a species group."""
        keepers: dict[int, str] = {}  # pokemon_id → reason
        er = self.enabled_rules

        # Rule 1: Best Overall
        if "BEST_OVERALL" in er:
            best = rules.best_overall(pokemon)
            if best:
                keepers[best.id] = "BEST_OVERALL"

        # Rule 2: Best Shiny
        if "BEST_SHINY" in er:
            best_s = rules.best_shiny(pokemon)
            if best_s and best_s.id not in keepers:
                keepers[best_s.id] = "BEST_SHINY"

        # Rule 3: Best Shadow
        if "BEST_SHADOW" in er:
            best_sh = rules.best_shadow(pokemon)
            if best_sh and best_sh.id not in keepers:
                keepers[best_sh.id] = "BEST_SHADOW"

        # Rule 4: Best Dynamax/Gmax
        if "BEST_DYNAMAX" in er:
            best_d = rules.best_dynamax(pokemon)
            if best_d and best_d.id not in keepers:
                keepers[best_d.id] = "BEST_DYNAMAX"

        # Rule 5: PvP candidates
        if self.pvp_db:
            species_id = species.lower().replace(" ", "_").replace("-", "_")
            for league, tag in [("little", "BEST_PVP_LL"), ("great", "BEST_PVP_GL"), ("ultra", "BEST_PVP_UL")]:
                if tag in er:
                    best_pvp = rules.best_pvp(pokemon, species_id, league, self.pvp_db)
                    if best_pvp and best_pvp.id not in keepers:
                        keepers[best_pvp.id] = tag

        # Rule 6: Size record holders
        for tag, reason in [
            ("LIGHTEST", "BEST_LIGHTEST"),
            ("HEAVIEST", "BEST_HEAVIEST"),
            ("SHORTEST", "BEST_SHORTEST"),
            ("TALLEST", "BEST_TALLEST"),
        ]:
            if reason in er:
                best_size = rules.best_size_tag(pokemon, tag)
                if best_size and best_size.id not in keepers:
                    keepers[best_size.id] = reason

        # Safety: never transfer the last of a species
        keeper_ids = set(keepers.keys())
        for p in pokemon:
            if p.id in keeper_ids:
                continue
            safety_reason = check_safety(p, pokemon, keeper_ids)
            if safety_reason:
                keepers[p.id] = safety_reason
                keeper_ids.add(p.id)

        # Apply decisions to database
        keeper_ids = set(keepers.keys())
        for pid, reason in keepers.items():
            self.db.update_decision(pid, "KEEP", reason)
        for p in pokemon:
            if p.id not in keeper_ids:
                self.db.update_decision(p.id, "TRANSFER", "")

        keep_count = len(keepers)
        transfer_count = len(pokemon) - keep_count

        if transfer_count > 0:
            log.info("  %s: %d keep, %d transfer", species, keep_count, transfer_count)

        return keep_count, transfer_count
