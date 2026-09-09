import sqlite3
import tempfile
import unittest
from pathlib import Path

from pokemgr.data.database import PokemonDatabase
from pokemgr.reader.screen import PokemonRead


def pokemon_read(
    species: str = "Zubat",
    cp: int = 10,
    atk: int = 0,
    def_: int = 12,
    sta: int = 15,
    hp: int = 12,
    **overrides,
) -> PokemonRead:
    values = {
        "species": species,
        "display_name": species,
        "cp": cp,
        "atk": atk,
        "def_": def_,
        "sta": sta,
        "shiny": False,
        "shadow": False,
        "favorited": False,
        "lucky": False,
        "hp": hp,
        "confidence": 0.9,
        "screenshot_path": f"{species.lower()}.png",
    }
    values.update(overrides)
    return PokemonRead(**values)


class PokemonDatabasePositionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "pokemon.db"
        self.db = PokemonDatabase(self.db_path)
        self.addCleanup(lambda: self.db.close())

    def test_identical_pokemon_at_distinct_positions_and_sessions_are_separate(self):
        self.db.create_session("session-a", "device")
        self.db.create_session("session-b", "device")
        read = pokemon_read()

        first_id = self.db.insert_pokemon(read, "session-a", 1)
        second_id = self.db.insert_pokemon(read, "session-a", 2)
        other_session_id = self.db.insert_pokemon(read, "session-b", 1)

        self.assertEqual(3, len({first_id, second_id, other_session_id}))
        self.assertEqual([1, 2], [p.position for p in self.db.get_all("session-a")])
        self.assertEqual(1, len(self.db.get_all("session-b")))
        self.assertEqual(0, self.db.remove_duplicates())
        self.assertEqual(2, len(self.db.get_all("session-a")))

    def test_retrying_same_session_position_updates_existing_row(self):
        self.db.create_session("session-a", "device")
        original_id = self.db.insert_pokemon(
            pokemon_read(), "session-a", 7
        )

        corrected_id = self.db.insert_pokemon(
            pokemon_read(
                species="Zorua",
                display_name="Buddy",
                cp=882,
                atk=3,
                def_=12,
                sta=15,
                hp=89,
                shiny=True,
                favorited=True,
                confidence=0.99,
                screenshot_path="corrected.png",
            ),
            "session-a",
            7,
        )

        self.assertEqual(original_id, corrected_id)
        rows = self.db.get_all("session-a")
        self.assertEqual(1, len(rows))
        self.assertEqual("Zorua", rows[0].species)
        self.assertEqual("Buddy", rows[0].display_name)
        self.assertEqual(882, rows[0].cp)
        self.assertEqual(
            (3, 12, 15, 89),
            (rows[0].atk, rows[0].def_, rows[0].sta, rows[0].hp),
        )
        self.assertTrue(rows[0].shiny)
        self.assertTrue(rows[0].favorited)
        self.assertEqual("corrected.png", rows[0].screenshot_path)
        self.assertAlmostEqual(0.99, rows[0].confidence)

    def test_existing_database_migration_keeps_latest_retry_and_adds_unique_index(self):
        self.db.create_session("legacy-session", "device")
        older_id = self.db.insert_pokemon(
            pokemon_read(species="Zubat", cp=10), "legacy-session", 3
        )
        self.db.flush()

        self.db.conn.execute("DROP INDEX uq_pokemon_session_position")
        cursor = self.db.conn.execute(
            """INSERT INTO pokemon
               (species, display_name, cp, atk, def_, sta, iv_total, iv_pct,
                shiny, shadow, lucky, favorited, gender, weight_tag, height_tag,
                is_dynamax, hp, position, screenshot_path, confidence,
                scan_session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "Zorua", "Zorua", 882, 3, 12, 15, 30, 30 / 45,
                0, 0, 0, 0, "none", "", "", 0, 89, 3,
                "newer.png", 0.95, "legacy-session",
            ),
        )
        newer_id = cursor.lastrowid
        self.assertGreater(newer_id, older_id)
        self.db.conn.commit()
        self.db.close()

        migrated = PokemonDatabase(self.db_path)
        self.db = migrated

        rows = migrated.get_all("legacy-session")
        self.assertEqual(1, len(rows))
        self.assertEqual(newer_id, rows[0].id)
        self.assertEqual("Zorua", rows[0].species)
        self.assertEqual(882, rows[0].cp)

        indexes = {
            row[1]: row[2]
            for row in migrated.conn.execute("PRAGMA index_list('pokemon')")
        }
        self.assertEqual(1, indexes["uq_pokemon_session_position"])
        schema_version = migrated.conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        self.assertEqual("2", schema_version)

        with self.assertRaises(sqlite3.IntegrityError):
            migrated.conn.execute(
                """INSERT INTO pokemon
                   (species, cp, atk, def_, sta, iv_total, iv_pct, position,
                    scan_session_id)
                   VALUES ('Abra', 100, 1, 2, 3, 6, 0.133, 3,
                           'legacy-session')"""
            )


if __name__ == "__main__":
    unittest.main()
