"""Individual decision rules for the keep/transfer engine."""

import logging

from ..data.models import Pokemon
from ..pvp.rankings_db import PvPRankingsDB

log = logging.getLogger(__name__)

# Match the caps used to build the IV tables in scripts/build_pvp_tables.py.
_LEAGUE_CP_CAPS = {"little": 500, "great": 1500, "ultra": 2500}


def _sort_key(p: Pokemon) -> tuple:
    """Default ranking: IV total desc, CP desc."""
    return (p.iv_total, p.cp)


def best_overall(pokemon_list: list[Pokemon]) -> Pokemon | None:
    """Find the best overall specimen: highest IV total, then CP."""
    if not pokemon_list:
        return None
    return max(pokemon_list, key=_sort_key)


def perfect_ivs(pokemon_list: list[Pokemon]) -> list[Pokemon]:
    """Keep every exact 15/15/15 occurrence, regardless of CP or category."""
    return [p for p in pokemon_list if (p.atk, p.def_, p.sta) == (15, 15, 15)]


def best_cp(pokemon_list: list[Pokemon]) -> Pokemon | None:
    """Keep the highest current CP, using IV total to break a CP tie."""
    if not pokemon_list:
        return None
    return max(pokemon_list, key=lambda p: (p.cp, p.iv_total))


def best_shiny(pokemon_list: list[Pokemon]) -> Pokemon | None:
    """Find the best shiny: highest IV total among shinies, then CP."""
    shinies = [p for p in pokemon_list if p.shiny]
    if not shinies:
        return None
    return max(shinies, key=_sort_key)


def best_shadow(pokemon_list: list[Pokemon]) -> Pokemon | None:
    """Find the best shadow: highest IV total among shadows, then CP."""
    shadows = [p for p in pokemon_list if p.shadow]
    if not shadows:
        return None
    return max(shadows, key=_sort_key)


def best_dynamax(pokemon_list: list[Pokemon]) -> Pokemon | None:
    """Find the best Dynamax/Gmax: highest IV total, then CP."""
    dynamax = [p for p in pokemon_list if p.is_dynamax]
    if not dynamax:
        return None
    return max(dynamax, key=_sort_key)


def best_size_tag(pokemon_list: list[Pokemon], tag: str) -> Pokemon | None:
    """Find the best Pokemon with a specific size tag (LIGHTEST, HEAVIEST, etc.)."""
    tagged = [p for p in pokemon_list
              if p.weight_tag == tag or p.height_tag == tag]
    if not tagged:
        return None
    return max(tagged, key=_sort_key)


def best_pvp(pokemon_list: list[Pokemon], species_id: str,
             league: str, pvp_db: PvPRankingsDB) -> Pokemon | None:
    """Find the best ranked candidate that can still enter this CP league."""
    cp_cap = _LEAGUE_CP_CAPS.get(league)
    if cp_cap is None:
        return None
    if not pvp_db.has_rankings(species_id, league):
        return None

    best = None
    best_rank = float("inf")

    for p in pokemon_list:
        if p.cp > cp_cap:
            continue
        rank = pvp_db.get_rank(species_id, league, p.atk, p.def_, p.sta)
        if rank is not None and rank < best_rank:
            best = p
            best_rank = rank

    if best and best_rank <= 200:
        log.debug("Best PvP %s for %s: rank %d (%s)",
                  league, species_id, best_rank, best.summary())
        return best

    return None
