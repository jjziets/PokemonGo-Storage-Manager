"""Keep pytest identity resolution independent of runtime caches and downloads."""

from copy import deepcopy
import json
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def public_species_catalog():
    """The complete parsed public catalog, not a narrowed candidate stub."""
    path = Path(__file__).parent / "fixtures/gamemaster-species.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or not catalog:
        raise ValueError("The checked-in test species catalog is empty or invalid")
    return catalog


@pytest.fixture(autouse=True)
def offline_species_resolution(monkeypatch, public_species_catalog):
    """Individual tests may still override these functions with smaller maps."""
    from pokemgr.decision import engine
    from pokemgr.pvp import resolver

    catalog = deepcopy(public_species_catalog)

    def species_map():
        return catalog

    monkeypatch.setattr(resolver, "_default_species_map", species_map)
    # DecisionEngine imports the function directly, so patch that bound alias
    # too. Runtime modules and their cache/download behavior remain unchanged.
    monkeypatch.setattr(engine, "_default_species_map", species_map)
    resolver._default_candy_index.cache_clear()
    try:
        yield
    finally:
        # Candy-family membership depends on the catalog and must not survive
        # into a test which supplies a different explicit map.
        resolver._default_candy_index.cache_clear()
