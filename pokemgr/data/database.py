"""SQLite database for Pokemon storage and scan sessions."""

import csv
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

from ..config import DB_PATH
from ..reader.screen import PokemonRead
from .models import Pokemon

log = logging.getLogger(__name__)


class PokemonDatabase:
    """SQLite database for storing scanned Pokemon data."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = str(db_path or DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL mode for concurrent reads + faster writes
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.create_tables()

    _pending_writes = 0

    def _maybe_commit(self):
        """Batch commits — flush every 10 writes instead of every single one."""
        self._pending_writes += 1
        if self._pending_writes >= 10:
            self.conn.commit()
            self._pending_writes = 0

    def flush(self):
        """Force commit any pending writes."""
        if self._pending_writes > 0:
            self.conn.commit()
            self._pending_writes = 0

    def create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS scan_sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                device_fingerprint TEXT,
                total_pokemon INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS pokemon (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                species TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                cp INTEGER NOT NULL,
                atk INTEGER NOT NULL,
                def_ INTEGER NOT NULL,
                sta INTEGER NOT NULL,
                iv_total INTEGER NOT NULL,
                iv_pct REAL NOT NULL,
                shiny INTEGER NOT NULL DEFAULT 0,
                shadow INTEGER NOT NULL DEFAULT 0,
                lucky INTEGER NOT NULL DEFAULT 0,
                favorited INTEGER NOT NULL DEFAULT 0,
                gender TEXT NOT NULL DEFAULT 'none',
                weight_tag TEXT NOT NULL DEFAULT '',
                height_tag TEXT NOT NULL DEFAULT '',
                is_dynamax INTEGER NOT NULL DEFAULT 0,
                hp INTEGER NOT NULL DEFAULT -1,
                position INTEGER NOT NULL,
                decision TEXT,
                decision_reason TEXT,
                pvp_rank_gl INTEGER,
                pvp_rank_ul INTEGER,
                screenshot_path TEXT,
                confidence REAL DEFAULT 0.0,
                scan_session_id TEXT,
                indexed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (scan_session_id) REFERENCES scan_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        # Indexes for dedup lookups and common queries
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pokemon_dedup ON pokemon (species, cp, atk, def_, sta, hp)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pokemon_session ON pokemon (scan_session_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pokemon_species ON pokemon (species)"
        )
        # Set schema version
        self.conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1')"
        )
        self.conn.commit()

    # ── Sessions ─────────────────────────────────────────────────────

    def create_session(self, session_id: str, device_fingerprint: str):
        self.conn.execute(
            "INSERT INTO scan_sessions (id, started_at, device_fingerprint) VALUES (?, ?, ?)",
            (session_id, datetime.now().isoformat(), device_fingerprint),
        )
        self.conn.commit()

    def complete_session(self, session_id: str, total_pokemon: int):
        self.conn.execute(
            "UPDATE scan_sessions SET completed_at = ?, total_pokemon = ? WHERE id = ?",
            (datetime.now().isoformat(), total_pokemon, session_id),
        )
        self.conn.commit()

    # ── Insert ───────────────────────────────────────────────────────

    def insert_pokemon(self, p: PokemonRead, session_id: str, position: int) -> int:
        """Insert a scanned Pokemon, deduplicating by species + CP + IVs + HP.

        If a matching Pokemon already exists, updates it with the latest scan data.
        Returns the Pokemon's row ID (existing or new).
        """
        iv_total = p.atk + p.def_ + p.sta
        iv_pct = iv_total / 45.0
        hp = getattr(p, 'hp', -1)

        # Check for existing duplicate (uses idx_pokemon_dedup index)
        existing = self.conn.execute(
            """SELECT id FROM pokemon
               WHERE species = ? AND cp = ? AND atk = ? AND def_ = ? AND sta = ? AND hp = ?
               LIMIT 1""",
            (p.species, p.cp, p.atk, p.def_, p.sta, hp),
        ).fetchone()

        if existing:
            row_id = existing[0]
            self.conn.execute(
                """UPDATE pokemon SET
                       display_name = ?, shiny = ?, shadow = ?, lucky = ?, favorited = ?,
                       gender = ?, weight_tag = ?, height_tag = ?, is_dynamax = ?,
                       position = ?, screenshot_path = ?, confidence = ?,
                       scan_session_id = ?, indexed_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    getattr(p, 'display_name', p.species),
                    int(p.shiny), int(p.shadow), int(p.lucky), int(p.favorited),
                    getattr(p, 'gender', 'none'),
                    getattr(p, 'weight_tag', ''),
                    getattr(p, 'height_tag', ''),
                    int(getattr(p, 'is_dynamax', False)),
                    position, p.screenshot_path, p.confidence, session_id,
                    row_id,
                ),
            )
            self._maybe_commit()
            log.debug("Dedup: updated existing Pokemon id=%d (%s CP%d)", row_id, p.species, p.cp)
            return row_id

        cursor = self.conn.execute(
            """INSERT INTO pokemon
               (species, display_name, cp, atk, def_, sta, iv_total, iv_pct,
                shiny, shadow, lucky, favorited, gender,
                weight_tag, height_tag, is_dynamax, hp,
                position, screenshot_path, confidence, scan_session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                p.species, getattr(p, 'display_name', p.species),
                p.cp, p.atk, p.def_, p.sta, iv_total, iv_pct,
                int(p.shiny), int(p.shadow), int(p.lucky), int(p.favorited),
                getattr(p, 'gender', 'none'),
                getattr(p, 'weight_tag', ''),
                getattr(p, 'height_tag', ''),
                int(getattr(p, 'is_dynamax', False)),
                hp,
                position, p.screenshot_path, p.confidence, session_id,
            ),
        )
        self._maybe_commit()
        return cursor.lastrowid

    # ── Queries ──────────────────────────────────────────────────────

    def _row_to_pokemon(self, row: sqlite3.Row) -> Pokemon:
        return Pokemon(
            id=row["id"],
            species=row["species"],
            display_name=row["display_name"] if "display_name" in row.keys() else row["species"],
            cp=row["cp"],
            atk=row["atk"],
            def_=row["def_"],
            sta=row["sta"],
            iv_total=row["iv_total"],
            iv_pct=row["iv_pct"],
            shiny=bool(row["shiny"]),
            shadow=bool(row["shadow"]),
            lucky=bool(row["lucky"]),
            favorited=bool(row["favorited"]),
            gender=row["gender"] if "gender" in row.keys() else "none",
            weight_tag=row["weight_tag"] if "weight_tag" in row.keys() else "",
            height_tag=row["height_tag"] if "height_tag" in row.keys() else "",
            is_dynamax=bool(row["is_dynamax"]) if "is_dynamax" in row.keys() else False,
            hp=row["hp"] if "hp" in row.keys() else -1,
            position=row["position"],
            decision=row["decision"],
            decision_reason=row["decision_reason"],
            pvp_rank_gl=row["pvp_rank_gl"],
            pvp_rank_ul=row["pvp_rank_ul"],
            screenshot_path=row["screenshot_path"] or "",
            confidence=row["confidence"] or 0.0,
            scan_session_id=row["scan_session_id"] or "",
        )

    def get_all(self, session_id: str | None = None) -> list[Pokemon]:
        self.flush()
        if session_id:
            rows = self.conn.execute(
                "SELECT * FROM pokemon WHERE scan_session_id = ? ORDER BY position",
                (session_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM pokemon ORDER BY position"
            ).fetchall()
        return [self._row_to_pokemon(r) for r in rows]

    def get_by_species(self, species: str,
                       session_id: str | None = None) -> list[Pokemon]:
        if session_id:
            rows = self.conn.execute(
                "SELECT * FROM pokemon WHERE species = ? AND scan_session_id = ?",
                (species, session_id),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM pokemon WHERE species = ?", (species,)
            ).fetchall()
        return [self._row_to_pokemon(r) for r in rows]

    def get_species_list(self, session_id: str | None = None) -> list[str]:
        if session_id:
            rows = self.conn.execute(
                "SELECT DISTINCT species FROM pokemon WHERE scan_session_id = ? ORDER BY species",
                (session_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT DISTINCT species FROM pokemon ORDER BY species"
            ).fetchall()
        return [r["species"] for r in rows]

    def find_duplicate(self, species: str, cp: int,
                       session_id: str) -> Pokemon | None:
        row = self.conn.execute(
            """SELECT * FROM pokemon
               WHERE species = ? AND cp = ? AND scan_session_id = ?
               LIMIT 1""",
            (species, cp, session_id),
        ).fetchone()
        return self._row_to_pokemon(row) if row else None

    # ── Updates ──────────────────────────────────────────────────────

    def update_decision(self, pokemon_id: int, decision: str, reason: str = ""):
        self.conn.execute(
            "UPDATE pokemon SET decision = ?, decision_reason = ? WHERE id = ?",
            (decision, reason, pokemon_id),
        )
        self._maybe_commit()

    def update_pvp_ranks(self, pokemon_id: int,
                         gl_rank: int | None, ul_rank: int | None):
        self.conn.execute(
            "UPDATE pokemon SET pvp_rank_gl = ?, pvp_rank_ul = ? WHERE id = ?",
            (gl_rank, ul_rank, pokemon_id),
        )
        self._maybe_commit()

    def update_favorited(self, pokemon_id: int, favorited: bool):
        self.conn.execute(
            "UPDATE pokemon SET favorited = ? WHERE id = ?",
            (int(favorited), pokemon_id),
        )
        self._maybe_commit()

    def clear_decisions(self, session_id: str | None = None):
        if session_id:
            self.conn.execute(
                "UPDATE pokemon SET decision = NULL, decision_reason = NULL WHERE scan_session_id = ?",
                (session_id,),
            )
        else:
            self.conn.execute(
                "UPDATE pokemon SET decision = NULL, decision_reason = NULL"
            )
        self.conn.commit()

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self, session_id: str | None = None) -> dict:
        self.flush()
        where = "WHERE scan_session_id = ?" if session_id else ""
        params = (session_id,) if session_id else ()

        total = self.conn.execute(
            f"SELECT COUNT(*) FROM pokemon {where}", params
        ).fetchone()[0]
        keep = self.conn.execute(
            f"SELECT COUNT(*) FROM pokemon {where} AND decision = 'KEEP'"
            if where else
            "SELECT COUNT(*) FROM pokemon WHERE decision = 'KEEP'",
            params,
        ).fetchone()[0]
        transfer = self.conn.execute(
            f"SELECT COUNT(*) FROM pokemon {where} AND decision = 'TRANSFER'"
            if where else
            "SELECT COUNT(*) FROM pokemon WHERE decision = 'TRANSFER'",
            params,
        ).fetchone()[0]

        return {
            "total": total,
            "keep": keep,
            "transfer": transfer,
            "undecided": total - keep - transfer,
        }

    # ── Export ────────────────────────────────────────────────────────

    def export_csv(self, path: str, session_id: str | None = None):
        """Export all Pokemon to a CSV file."""
        pokemon = self.get_all(session_id)
        if not pokemon:
            log.warning("No Pokemon to export")
            return

        fieldnames = [
            "id", "species", "cp", "atk", "def_", "sta",
            "iv_total", "iv_pct", "shiny", "shadow", "lucky", "favorited",
            "position", "decision", "decision_reason",
            "pvp_rank_gl", "pvp_rank_ul", "confidence",
        ]

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for p in pokemon:
                row = {
                    "id": p.id,
                    "species": p.species,
                    "cp": p.cp,
                    "atk": p.atk,
                    "def_": p.def_,
                    "sta": p.sta,
                    "iv_total": p.iv_total,
                    "iv_pct": f"{p.iv_pct:.4f}",
                    "shiny": int(p.shiny),
                    "shadow": int(p.shadow),
                    "lucky": int(p.lucky),
                    "favorited": int(p.favorited),
                    "position": p.position,
                    "decision": p.decision or "",
                    "decision_reason": p.decision_reason or "",
                    "pvp_rank_gl": p.pvp_rank_gl or "",
                    "pvp_rank_ul": p.pvp_rank_ul or "",
                    "confidence": f"{p.confidence:.3f}",
                }
                writer.writerow(row)

        log.info("Exported %d Pokemon to %s", len(pokemon), path)

    def remove_duplicates(self) -> int:
        """Remove duplicate Pokemon, keeping the one with the highest id (latest scan).

        Duplicates are defined as same species + CP + IVs (atk/def/sta) + HP.
        Returns the number of rows deleted.
        """
        cursor = self.conn.execute(
            """DELETE FROM pokemon WHERE id NOT IN (
                   SELECT MAX(id) FROM pokemon
                   GROUP BY species, cp, atk, def_, sta, hp
               )"""
        )
        deleted = cursor.rowcount
        self.conn.commit()
        if deleted > 0:
            log.info("Removed %d duplicate Pokemon", deleted)
        return deleted

    def close(self):
        self.flush()
        self.conn.close()
