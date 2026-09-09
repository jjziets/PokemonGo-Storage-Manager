"""PvP IV ranking calculator — computes stat products for all 4096 IV combos."""

import math
import logging
from dataclasses import dataclass

from .cpm_table import CPM_TABLE, LEVELS_DESCENDING

log = logging.getLogger(__name__)


@dataclass
class IVRanking:
    """A single IV combination's ranking for a specific league."""
    rank: int
    atk_iv: int
    def_iv: int
    sta_iv: int
    level: float
    cp: int
    stat_product: float


def compute_cp(base_atk: int, base_def: int, base_sta: int,
               iv_atk: int, iv_def: int, iv_sta: int, cpm: float) -> int:
    """Calculate Pokemon Go CP using the standard formula."""
    # CP is floored even when it lies very close to the next integer. A
    # blanket round-up threshold invents CP values for valid fractional results.
    raw = (
        (base_atk + iv_atk)
        * math.sqrt(base_def + iv_def)
        * math.sqrt(base_sta + iv_sta)
        * cpm ** 2 / 10
    )
    return max(10, math.floor(raw))


def compute_stat_product(base_atk: int, base_def: int, base_sta: int,
                         iv_atk: int, iv_def: int, iv_sta: int,
                         cpm: float) -> float:
    """Calculate the stat product (ATK * DEF * floor(STA))."""
    atk = (base_atk + iv_atk) * cpm
    def_ = (base_def + iv_def) * cpm
    sta = math.floor((base_sta + iv_sta) * cpm)
    return atk * def_ * sta


def rank_ivs_for_league(base_atk: int, base_def: int, base_sta: int,
                        cp_cap: int, max_level: float = 51.0) -> list[IVRanking]:
    """Rank all 4096 IV combinations for a specific league CP cap.

    Returns a sorted list of IVRanking objects, rank 1 = best PvP IVs.
    """
    results = []

    for iv_atk in range(16):
        for iv_def in range(16):
            for iv_sta in range(16):
                # Find highest level where CP <= cp_cap
                best_level = None
                best_cp = None
                best_sp = None

                for level in LEVELS_DESCENDING:
                    if level > max_level:
                        continue
                    cpm = CPM_TABLE[level]
                    cp = compute_cp(base_atk, base_def, base_sta,
                                    iv_atk, iv_def, iv_sta, cpm)
                    if cp <= cp_cap:
                        sp = compute_stat_product(
                            base_atk, base_def, base_sta,
                            iv_atk, iv_def, iv_sta, cpm,
                        )
                        best_level = level
                        best_cp = cp
                        best_sp = sp
                        break

                if best_sp is not None:
                    results.append((iv_atk, iv_def, iv_sta,
                                    best_level, best_cp, best_sp))

    # Sort by stat product descending
    results.sort(key=lambda x: x[5], reverse=True)

    # Assign ranks
    rankings = []
    for i, (iv_atk, iv_def, iv_sta, level, cp, sp) in enumerate(results):
        rankings.append(IVRanking(
            rank=i + 1,
            atk_iv=iv_atk,
            def_iv=iv_def,
            sta_iv=iv_sta,
            level=level,
            cp=cp,
            stat_product=sp,
        ))

    return rankings


def get_rank(rankings: list[IVRanking], atk: int, def_: int, sta: int) -> int | None:
    """Look up the rank for a specific IV combination."""
    for r in rankings:
        if r.atk_iv == atk and r.def_iv == def_ and r.sta_iv == sta:
            return r.rank
    return None
