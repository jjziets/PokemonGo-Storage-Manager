"""Safety rules — prevent accidental transfer of the last of a species."""

import logging

from ..data.models import Pokemon

log = logging.getLogger(__name__)


def is_last_of_species(pokemon: Pokemon, species_group: list[Pokemon],
                       keeper_ids: set[int]) -> bool:
    """Return True if transferring this Pokemon would leave zero of its species."""
    kept = [p for p in species_group if p.id in keeper_ids]
    if len(kept) == 0:
        log.info("Safety: %s is last of species, keeping", pokemon.species)
        return True
    return False


def check_safety(pokemon: Pokemon, species_group: list[Pokemon],
                 keeper_ids: set[int]) -> str | None:
    """Run safety checks. Returns a reason string if kept, else None."""
    if is_last_of_species(pokemon, species_group, keeper_ids):
        return "LAST_OF_SPECIES"
    return None
