"""SQLite storage for precomputed PvP IV rankings."""

import sqlite3
import logging
from pathlib import Path

from ..config import PVP_DB_PATH
from .calculator import IVRanking

log = logging.getLogger(__name__)


class PvPRankingsDB:
    """Stores and queries precomputed PvP IV rankings per species and league."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = str(db_path or PVP_DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS pvp_rankings (
                species_id TEXT NOT NULL,
                league TEXT NOT NULL,
                rank INTEGER NOT NULL,
                atk_iv INTEGER NOT NULL,
                def_iv INTEGER NOT NULL,
                sta_iv INTEGER NOT NULL,
                level REAL NOT NULL,
                cp INTEGER NOT NULL,
                stat_product REAL NOT NULL,
                PRIMARY KEY (species_id, league, rank)
            );

            CREATE TABLE IF NOT EXISTS build_info (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_pvp_species_league
                ON pvp_rankings (species_id, league);
        """)
        self.conn.commit()

    def store_rankings(self, species_id: str, league: str,
                       rankings: list[IVRanking], top_n: int = 100):
        """Store the top N rankings for a species in a league."""
        # Delete existing rankings for this species/league
        self.conn.execute(
            "DELETE FROM pvp_rankings WHERE species_id = ? AND league = ?",
            (species_id, league),
        )

        for r in rankings[:top_n]:
            self.conn.execute(
                """INSERT INTO pvp_rankings
                   (species_id, league, rank, atk_iv, def_iv, sta_iv, level, cp, stat_product)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (species_id, league, r.rank, r.atk_iv, r.def_iv, r.sta_iv,
                 r.level, r.cp, r.stat_product),
            )
        self.conn.commit()

    def get_rank(self, species_id: str, league: str,
                 atk: int, def_: int, sta: int) -> int | None:
        """Look up the PvP rank for a specific IV combination."""
        row = self.conn.execute(
            """SELECT rank FROM pvp_rankings
               WHERE species_id = ? AND league = ? AND atk_iv = ? AND def_iv = ? AND sta_iv = ?""",
            (species_id, league, atk, def_, sta),
        ).fetchone()
        return row["rank"] if row else None

    def get_top_rankings(self, species_id: str, league: str,
                         limit: int = 10) -> list[IVRanking]:
        """Get the top-ranked IV spreads for a species in a league."""
        rows = self.conn.execute(
            """SELECT * FROM pvp_rankings
               WHERE species_id = ? AND league = ?
               ORDER BY rank LIMIT ?""",
            (species_id, league, limit),
        ).fetchall()
        return [
            IVRanking(
                rank=r["rank"], atk_iv=r["atk_iv"], def_iv=r["def_iv"],
                sta_iv=r["sta_iv"], level=r["level"], cp=r["cp"],
                stat_product=r["stat_product"],
            )
            for r in rows
        ]

    def has_rankings(self, species_id: str, league: str) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM pvp_rankings WHERE species_id = ? AND league = ?",
            (species_id, league),
        ).fetchone()
        return row[0] > 0

    def get_all_species(self) -> list[str]:
        """List all species that have precomputed rankings."""
        rows = self.conn.execute(
            "SELECT DISTINCT species_id FROM pvp_rankings ORDER BY species_id"
        ).fetchall()
        return [r["species_id"] for r in rows]

    def set_build_info(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO build_info (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def get_build_info(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM build_info WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def close(self):
        self.conn.close()
