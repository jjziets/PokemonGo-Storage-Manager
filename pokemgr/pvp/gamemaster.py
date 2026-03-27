"""Download and parse PvPoke gamemaster.json for Pokemon base stats."""

import json
import logging
from pathlib import Path

import requests

from ..config import CACHE_DIR

log = logging.getLogger(__name__)

GAMEMASTER_URL = "https://raw.githubusercontent.com/pvpoke/pvpoke/master/src/data/gamemaster.json"
GAMEMASTER_CACHE = CACHE_DIR / "gamemaster.json"


def download_gamemaster(force: bool = False) -> dict:
    """Download gamemaster.json from PvPoke GitHub, using local cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if GAMEMASTER_CACHE.exists() and not force:
        log.info("Using cached gamemaster.json")
        return json.loads(GAMEMASTER_CACHE.read_text())

    log.info("Downloading gamemaster.json from PvPoke...")
    resp = requests.get(GAMEMASTER_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    GAMEMASTER_CACHE.write_text(json.dumps(data, indent=2))
    log.info("Cached gamemaster.json (%d bytes)", len(resp.content))
    return data


def parse_species(gamemaster: dict) -> dict[str, dict]:
    """Extract species base stats from the gamemaster.

    Returns dict mapping species_id to:
        {
            "name": "Swampert",
            "id": "swampert",
            "base_atk": 208,
            "base_def": 175,
            "base_sta": 225,
            "types": ["water", "ground"],
            "evolutions": [...],
        }
    """
    species_map = {}

    pokemon_list = gamemaster.get("pokemon", [])
    for entry in pokemon_list:
        species_id = entry.get("speciesId", "")
        if not species_id:
            continue

        base_stats = entry.get("baseStats", {})
        if not base_stats:
            continue

        species_map[species_id] = {
            "name": entry.get("speciesName", species_id),
            "id": species_id,
            "base_atk": base_stats.get("atk", 0),
            "base_def": base_stats.get("def", 0),
            "base_sta": base_stats.get("hp", 0),
            "types": entry.get("types", []),
            "evolutions": _parse_evolutions(entry),
        }

    log.info("Parsed %d species from gamemaster", len(species_map))
    return species_map


def _parse_evolutions(entry: dict) -> list[str]:
    """Extract evolution chain from a gamemaster entry."""
    evolutions = []
    for evo in entry.get("evolutions", []):
        if isinstance(evo, str):
            evolutions.append(evo)
        elif isinstance(evo, dict):
            evolutions.append(evo.get("speciesId", ""))
    return [e for e in evolutions if e]


def get_species_base_stats(species_id: str, species_map: dict) -> dict | None:
    """Look up base stats for a species."""
    # Try exact match first
    if species_id in species_map:
        return species_map[species_id]

    # Try lowercase
    lower = species_id.lower().replace(" ", "_").replace("-", "_")
    if lower in species_map:
        return species_map[lower]

    # Fuzzy: try matching by name
    for sid, data in species_map.items():
        if data["name"].lower() == species_id.lower():
            return data

    return None
