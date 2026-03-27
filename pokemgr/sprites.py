"""Pokemon sprite downloader and cache.

Downloads sprites from PokeAPI's GitHub repo on demand and caches locally.
Sprites are 96x96 PNG images.
"""

import logging
import json
from pathlib import Path

import requests

from .config import CACHE_DIR

log = logging.getLogger(__name__)

SPRITES_DIR = CACHE_DIR / "sprites"
SPECIES_MAP_PATH = CACHE_DIR / "species_to_dex.json"

# PokeAPI raw sprites (no rate limit, public GitHub)
SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{dex_id}.png"

# Common name → Pokedex number mapping (Gen 1-3 covers most of what you'll encounter)
# This is a fallback; we also try to load from gamemaster
_NAME_TO_DEX: dict[str, int] = {}


def _load_species_map() -> dict[str, int]:
    """Load or build the species name → Pokedex number mapping."""
    global _NAME_TO_DEX

    if _NAME_TO_DEX:
        return _NAME_TO_DEX

    # Try cached map first
    if SPECIES_MAP_PATH.exists():
        try:
            _NAME_TO_DEX = json.loads(SPECIES_MAP_PATH.read_text())
            return _NAME_TO_DEX
        except Exception:
            pass

    # Build from a hardcoded Gen 1-9 list (covers 1025 Pokemon)
    # We'll fetch from PokeAPI's pokemon-species endpoint
    try:
        log.info("Downloading species list from PokeAPI...")
        resp = requests.get(
            "https://pokeapi.co/api/v2/pokemon-species?limit=1025",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for entry in data.get("results", []):
            name = entry["name"].capitalize()
            # URL format: .../pokemon-species/{id}/
            dex_id = int(entry["url"].rstrip("/").split("/")[-1])
            _NAME_TO_DEX[name.lower()] = dex_id

            # Also add with capital
            _NAME_TO_DEX[name] = dex_id

        # Cache it
        SPRITES_DIR.mkdir(parents=True, exist_ok=True)
        SPECIES_MAP_PATH.write_text(json.dumps(_NAME_TO_DEX))
        log.info("Cached %d species mappings", len(_NAME_TO_DEX) // 2)

    except Exception as e:
        log.warning("Failed to download species list: %s — using built-in fallback", e)
        _NAME_TO_DEX = _BUILTIN_MAP.copy()

    return _NAME_TO_DEX


def get_dex_number(species_name: str) -> int | None:
    """Look up the Pokedex number for a species name."""
    name_map = _load_species_map()

    # Try exact match
    name = species_name.strip()
    if name.lower() in name_map:
        return name_map[name.lower()]

    # Try removing common suffixes (renamed Pokemon)
    # e.g. "MachampG" → "Machamp"
    for suffix in ["G", "S", " Shadow", " Purified"]:
        cleaned = name.rstrip(suffix).strip()
        if cleaned.lower() in name_map:
            return name_map[cleaned.lower()]

    return None


_download_pending: set[int] = set()


def get_sprite_path(species_name: str, allow_download: bool = False) -> Path | None:
    """Get the local path to a Pokemon's sprite.

    Returns the cached path immediately or None if not cached.
    Set allow_download=True to block and download (use from background threads only).
    """
    dex_id = get_dex_number(species_name)
    if dex_id is None:
        return None

    SPRITES_DIR.mkdir(parents=True, exist_ok=True)
    path = SPRITES_DIR / f"{dex_id}.png"

    if path.exists():
        return path

    if not allow_download:
        # Queue background download (non-blocking)
        if dex_id not in _download_pending:
            _download_pending.add(dex_id)
            import threading
            threading.Thread(target=_download_sprite, args=(dex_id,), daemon=True).start()
        return None

    # Blocking download
    return _download_sprite(dex_id)


def _download_sprite(dex_id: int) -> Path | None:
    """Download a sprite from PokeAPI (runs in background thread)."""
    path = SPRITES_DIR / f"{dex_id}.png"
    if path.exists():
        _download_pending.discard(dex_id)
        return path
    try:
        url = SPRITE_URL.format(dex_id=dex_id)
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        path.write_bytes(resp.content)
        _download_pending.discard(dex_id)
        return path
    except Exception as e:
        log.debug("Failed to download sprite for #%d: %s", dex_id, e)
        _download_pending.discard(dex_id)
        return None


# Built-in fallback for Gen 1 (most common)
_BUILTIN_MAP = {
    "bulbasaur": 1, "ivysaur": 2, "venusaur": 3,
    "charmander": 4, "charmeleon": 5, "charizard": 6,
    "squirtle": 7, "wartortle": 8, "blastoise": 9,
    "caterpie": 10, "metapod": 11, "butterfree": 12,
    "weedle": 13, "kakuna": 14, "beedrill": 15,
    "pidgey": 16, "pidgeotto": 17, "pidgeot": 18,
    "rattata": 19, "raticate": 20,
    "spearow": 21, "fearow": 22,
    "ekans": 23, "arbok": 24,
    "pikachu": 25, "raichu": 26,
    "sandshrew": 27, "sandslash": 28,
    "nidoran-f": 29, "nidorina": 30, "nidoqueen": 31,
    "nidoran-m": 32, "nidorino": 33, "nidoking": 34,
    "clefairy": 35, "clefable": 36,
    "vulpix": 37, "ninetales": 38,
    "jigglypuff": 39, "wigglytuff": 40,
    "zubat": 41, "golbat": 42,
    "oddish": 43, "gloom": 44, "vileplume": 45,
    "paras": 46, "parasect": 47,
    "venonat": 48, "venomoth": 49,
    "diglett": 50, "dugtrio": 51,
    "meowth": 52, "persian": 53,
    "psyduck": 54, "golduck": 55,
    "mankey": 56, "primeape": 57,
    "growlithe": 58, "arcanine": 59,
    "poliwag": 60, "poliwhirl": 61, "poliwrath": 62,
    "abra": 63, "kadabra": 64, "alakazam": 65,
    "machop": 66, "machoke": 67, "machamp": 68,
    "bellsprout": 69, "weepinbell": 70, "victreebel": 71,
    "tentacool": 72, "tentacruel": 73,
    "geodude": 74, "graveler": 75, "golem": 76,
    "ponyta": 77, "rapidash": 78,
    "slowpoke": 79, "slowbro": 80,
    "magnemite": 81, "magneton": 82,
    "farfetch'd": 83, "farfetchd": 83,
    "doduo": 84, "dodrio": 85,
    "seel": 86, "dewgong": 87,
    "grimer": 88, "muk": 89,
    "shellder": 90, "cloyster": 91,
    "gastly": 92, "haunter": 93, "gengar": 94,
    "onix": 95,
    "drowzee": 96, "hypno": 97,
    "krabby": 98, "kingler": 99,
    "voltorb": 100, "electrode": 101,
    "exeggcute": 102, "exeggutor": 103,
    "cubone": 104, "marowak": 105,
    "hitmonlee": 106, "hitmonchan": 107,
    "lickitung": 108,
    "koffing": 109, "weezing": 110,
    "rhyhorn": 111, "rhydon": 112,
    "chansey": 113,
    "tangela": 114,
    "kangaskhan": 115,
    "horsea": 116, "seadra": 117,
    "goldeen": 118, "seaking": 119,
    "staryu": 120, "starmie": 121,
    "mr. mime": 122, "mr.mime": 122,
    "scyther": 123,
    "jynx": 124,
    "electabuzz": 125,
    "magmar": 126,
    "pinsir": 127,
    "tauros": 128,
    "magikarp": 129, "gyarados": 130,
    "lapras": 131,
    "ditto": 132,
    "eevee": 133, "vaporeon": 134, "jolteon": 135, "flareon": 136,
    "porygon": 137,
    "omanyte": 138, "omastar": 139,
    "kabuto": 140, "kabutops": 141,
    "aerodactyl": 142,
    "snorlax": 143,
    "articuno": 144, "zapdos": 145, "moltres": 146,
    "dratini": 147, "dragonair": 148, "dragonite": 149,
    "mewtwo": 150, "mew": 151,
}
