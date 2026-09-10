"""Persist observed stars without assigning a read to an arbitrary duplicate."""

from collections import Counter, defaultdict


# TRACEWEAVER: file-role=confirmed-star-persistence; req=REQ-MASS-001,REQ-DATA-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
class FavoriteSync:
    def __init__(self, db, rows, key_for, *, changes_only=False):
        self.db = db
        self.rows = rows
        self.changes_only = changes_only
        self.groups = defaultdict(list)
        for row in rows:
            self.groups[key_for(row)].append(row)
        self.synced = set()
        self.observations = Counter()
        self.all_observations = Counter()
        self.confirmed = 0
        self.uniform_complete = False
        self.error = None

    def new_traversal(self):
        # Reopened CP ties may be ordered differently. Never add repeated
        # observations from a new traversal to a partial duplicate group.
        # Confirmed OFF-to-ON changes are unique mutation evidence, however:
        # the caller never supplies already-ON observations in this mode.
        if not self.changes_only:
            self.observations.clear()

    def _save(self, rows, target):
        try:
            ids = [row.id for row in rows]
            self.db.update_favorited_many(ids, target)
        except Exception as exc:
            self.error = f"Phone star confirmed, but favorite status could not be saved: {exc}"
            return False
        self.synced.update(ids)
        return True

    def observe(self, key, target):
        """Called only after a same-position affirmative star readback."""
        if self.changes_only and target is not True:
            raise ValueError("Changes-only favorite sync requires a confirmed OFF-to-ON change")
        self.confirmed += 1
        self.all_observations[key] += 1
        rows = self.groups.get(key, ()) if key is not None else ()
        if (not rows or len({row.scan_session_id for row in rows}) != 1
                or any(type(getattr(row, "id", None)) is not int for row in rows)):
            return
        if self.changes_only and (any(row.id <= 0 for row in rows)
                                  or len({row.id for row in rows}) != len(rows)):
            return
        self.observations[key] += 1
        if (self.observations[key] == len(rows)
                or (not self.changes_only and len(rows) == 1)):
            self._save(rows, target)

    def complete_uniform_pass(self, query, target):
        """A fully verified pass proves the same final state for its scope.

        This intentionally supports only membership represented in our schema.
        CP-free category reads cannot map a partial pass onto individual rows.
        """
        if self.changes_only:
            return
        predicates = {
            "cp0-": lambda row: True,
            "shiny": lambda row: row.shiny,
            "shadow": lambda row: row.shadow,
            "lucky": lambda row: row.lucky,
            "4*": lambda row: (row.atk, row.def_, row.sta) == (15, 15, 15),
            "3*": lambda row: 37 <= row.atk + row.def_ + row.sta <= 44,
        }
        predicate = predicates.get(query.strip().casefold())
        if predicate is None:
            return
        rows = [row for row in self.rows if predicate(row)]
        if self._save(rows, target):
            self.uniform_complete = True

    def result(self):
        unresolved = 0
        for key, count in self.all_observations.items():
            rows = self.groups.get(key, ()) if key is not None else ()
            if not rows or not all(getattr(row, "id", None) in self.synced for row in rows):
                unresolved += count
            elif self.changes_only:
                # Extra live changes cannot be assigned to a saved group with
                # fewer stored occurrences, even though that group is complete.
                unresolved += max(0, count - len(rows))
        result = {"db_synced": len(self.synced),
                  "db_unresolved": 0 if self.uniform_complete else unresolved}
        if self.error:
            result["error"] = self.error
        if result["db_unresolved"]:
            result["note"] = (
                "Some confirmed phone stars could not be assigned to stored records. "
                "Their recorded status is unchanged; rescan those Pokemon before relying "
                "on the saved favorite counts."
            )
        return result
