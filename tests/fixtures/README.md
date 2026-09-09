# Public species fixture

`gamemaster-species.json` contains all **1710** entries returned by
`pokemgr.pvp.gamemaster.parse_species` for the project's cached public PvPoke
GameMaster. It is the full parsed species map: global uniqueness and competing
forms must be tested against the complete catalog, not a handpicked subset.

- Upstream source: https://raw.githubusercontent.com/pvpoke/pvpoke/master/src/data/gamemaster.json
- Embedded upstream timestamp: `2026-03-24 02:09:40` (timezone unspecified).
- Original raw file SHA-256: `511decddfbd767f4e7b77a4a1d76e4f3d9561ce10b0af3bf41083c4d2de0e620`.
- Parsed fixture SHA-256: `5f81cbe2d330a47afa542122d47130cd7b7a266adfe807cfedaa95c17794d2e5`.
- The original download's upstream Git revision was not recorded. The hashes
  identify the exact input and derived fixture; the mutable URL does not.

The retained fields are `id`, `name`, `base_atk`, `base_def`, `base_sta`, `types`,
`evolutions`, `family_id`, and `dex`. Base stats and full form IDs support exact
CP/HP/IV resolution; family IDs and dex numbers support candy-family grouping.
Evolution/type data is retained so this stays a complete `parse_species` result.
There are no trainer names, device identifiers, captured screens, collection
records, account resources, or other private account data in this file.

`tests/conftest.py` supplies an isolated copy to each pytest test through the
resolver and DecisionEngine's imported catalog function. Per-test mocks take
precedence, and the candy index is cleared between tests. The runtime loader is
unchanged. Direct `unittest` execution does not load pytest's conftest; use
pytest for the deterministic offline suite.

To update this fixture deliberately, obtain the desired public GameMaster,
record its original SHA-256 and timestamp, and run `parse_species` on it. Write
all resulting entries sorted by species ID, one compact JSON entry per line,
using sorted field keys and UTF-8. Update both hashes and the entry count here,
then run the snapshot, resolver, candy-family, and decision regressions without
runtime cache files or network access. Never refresh the fixture during tests.

The `reader/` PNG fixtures are cropped favorite-star glyphs; private full-screen
capture regressions remain optional local data outside this fixture directory.
