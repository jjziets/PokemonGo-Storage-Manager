"""Decision review panel — keep/transfer lists with manual overrides."""

import html
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget,
    QTreeWidgetItem, QPushButton, QLabel, QMessageBox, QGroupBox,
    QProgressBar, QTextEdit, QCheckBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from ...data.models import Pokemon


class DecisionReview(QWidget):
    """Two-panel view: KEEP list (left) and TRANSFER list (right)."""

    decisions_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pokemon_map: dict[int, Pokemon] = {}
        self._fav_active = False
        self._fav_dry_run = False
        self._fav_action = "Favoriting"
        self._fav_last_error = None
        self._fav_paused_at = None
        self._fav_paused_total = 0.
        self._fav_last_progress = 0
        self._fav_stopping = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Top bar: actions + stats ──
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)

        self.run_engine_btn = QPushButton("Run Decision Engine")
        self.run_engine_btn.setStyleSheet(
            "QPushButton { background-color: #2a5a7a; padding: 6px 12px; font-weight: bold; }"
        )
        self.run_engine_btn.setToolTip("Apply the selected keep rules to the stored scan stats.")
        top_bar.addWidget(self.run_engine_btn)

        self.unfavorite_btn = QPushButton("Unfavorite All")
        self.unfavorite_btn.setStyleSheet("QPushButton { background-color: #5a3a3a; padding: 6px 12px; }")
        self.unfavorite_btn.setToolTip("Remove favorite star from ALL Pokemon")
        top_bar.addWidget(self.unfavorite_btn)

        self.approve_btn = QPushButton("Fav Keepers (Dry)")
        self.approve_btn.setStyleSheet(
            "QPushButton { background-color: #4a5a2a; padding: 6px 12px; font-weight: bold; }"
        )
        self.approve_btn.setToolTip(
            "Dry run: exclude existing favorites on the phone. Read appraisal HP and IVs first, "
            "then verify species/form, CP and flags "
            "against stored keeper stats. Nicknames do not determine matches."
        )
        top_bar.addWidget(self.approve_btn)

        self.favorite_real_btn = QPushButton("Fav Keepers (REAL)")
        self.favorite_real_btn.setStyleSheet(
            "QPushButton { background-color: #7a6a2a; padding: 6px 12px; font-weight: bold; }"
        )
        self.favorite_real_btn.setToolTip(
            "Exclude existing favorites on the phone. Read appraisal HP and IVs first, "
            "then favorite only keepers whose species/form, "
            "CP and flags match the stored stats. Nicknames do not determine matches."
        )
        top_bar.addWidget(self.favorite_real_btn)

        top_bar.addStretch()
        layout.addLayout(top_bar)

        # ── Keep rules selection ──
        rules_bar1 = QHBoxLayout()
        rules_bar1.setSpacing(6)
        rules_bar1.addWidget(QLabel("Keep rules:"))

        self.rule_checks = {}
        rule_defs = [
            ("BEST_OVERALL",  "Best Overall",  True),
            ("KEEP_PERFECT_IV", "All 100% IVs", True),
            ("BEST_CP",       "Highest CP",    True),
            ("BEST_SHINY",    "Best Shiny",    True),
            ("BEST_SHADOW",   "Best Shadow",   True),
            ("BEST_DYNAMAX",  "Best Dynamax",  True),
            ("BEST_PVP_LL",   "PvP Little",    False),
            ("BEST_PVP_GL",   "PvP Great",     True),
            ("BEST_PVP_UL",   "PvP Ultra",     True),
        ]
        for rule_id, label, default in rule_defs:
            cb = QCheckBox(label)
            cb.setChecked(default)
            if rule_id == "KEEP_PERFECT_IV":
                cb.setToolTip(
                    "Keep every exact 15/15/15 Pokémon, including duplicates. "
                    "Uncheck to disable this rule."
                )
            elif rule_id == "BEST_CP":
                cb.setToolTip(
                    "Keep the highest current CP per species/form alongside the best IV and PvP picks."
                )
            self.rule_checks[rule_id] = cb
            rules_bar1.addWidget(cb)

        rules_bar1.addStretch()
        layout.addLayout(rules_bar1)

        rules_bar2 = QHBoxLayout()
        rules_bar2.setSpacing(6)
        rules_bar2.addWidget(QLabel("Size rules:"))

        size_rules = [
            ("BEST_LIGHTEST", "Lightest", False),
            ("BEST_HEAVIEST", "Heaviest", False),
            ("BEST_SHORTEST", "Shortest", False),
            ("BEST_TALLEST",  "Tallest",  False),
        ]
        for rule_id, label, default in size_rules:
            cb = QCheckBox(label)
            cb.setChecked(default)
            self.rule_checks[rule_id] = cb
            rules_bar2.addWidget(cb)

        rules_bar2.addStretch()
        layout.addLayout(rules_bar2)

        # ── Favorite pass selection ──
        pass_bar = QHBoxLayout()
        pass_bar.setSpacing(6)
        pass_bar.addWidget(QLabel("Fav passes:"))

        self.fav_pass_normal = QCheckBox("Normal")
        self.fav_pass_normal.setChecked(True)
        pass_bar.addWidget(self.fav_pass_normal)

        self.fav_pass_shiny = QCheckBox("Shiny")
        self.fav_pass_shiny.setChecked(True)
        pass_bar.addWidget(self.fav_pass_shiny)

        self.fav_pass_shadow = QCheckBox("Shadow")
        self.fav_pass_shadow.setChecked(True)
        pass_bar.addWidget(self.fav_pass_shadow)

        self.fav_pass_dynamax = QCheckBox("Dynamax")
        self.fav_pass_dynamax.setChecked(True)
        pass_bar.addWidget(self.fav_pass_dynamax)

        self.fav_pass_gmax = QCheckBox("Gigantamax")
        self.fav_pass_gmax.setChecked(True)
        pass_bar.addWidget(self.fav_pass_gmax)

        pass_bar.addStretch()

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #aaa;")
        pass_bar.addWidget(self.stats_label)

        layout.addLayout(pass_bar)

        # ── Favorite progress (hidden until active) ──
        self.fav_progress_bar = QProgressBar()
        self.fav_progress_bar.setTextVisible(True)
        self.fav_progress_bar.setFormat("Ready")
        self.fav_progress_bar.setMinimum(0)
        self.fav_progress_bar.setMaximum(100)
        self.fav_progress_bar.setValue(0)
        self.fav_progress_bar.setMaximumHeight(20)
        self.fav_progress_bar.setVisible(False)
        layout.addWidget(self.fav_progress_bar)

        fav_ctrl_layout = QHBoxLayout()
        fav_ctrl_layout.setSpacing(4)
        self.fav_status_label = QLabel("")
        self.fav_status_label.setTextFormat(Qt.PlainText)
        self.fav_status_label.setWordWrap(True)
        self.fav_status_label.setStyleSheet("font-weight: bold; color: #fc8;")
        fav_ctrl_layout.addWidget(self.fav_status_label)
        fav_ctrl_layout.addStretch()

        self.fav_pause_btn = QPushButton("Pause")
        self.fav_pause_btn.setVisible(False)
        fav_ctrl_layout.addWidget(self.fav_pause_btn)

        self.fav_stop_btn = QPushButton("Stop")
        self.fav_stop_btn.setStyleSheet("QPushButton { background-color: #7a2a2a; }")
        self.fav_stop_btn.setVisible(False)
        fav_ctrl_layout.addWidget(self.fav_stop_btn)

        layout.addLayout(fav_ctrl_layout)

        self.fav_log = QTextEdit()
        self.fav_log.setReadOnly(True)
        self.fav_log.setMaximumHeight(120)
        self.fav_log.setVisible(False)
        self.fav_log.setStyleSheet(
            "QTextEdit { font-family: monospace; font-size: 11px; background-color: #1a1a2a; color: #ccc; }"
        )
        layout.addWidget(self.fav_log)

        # ── Splitter: KEEP | TRANSFER (takes all remaining space) ──
        splitter = QSplitter(Qt.Horizontal)

        # Keep panel
        keep_group = QGroupBox("KEEP")
        keep_group.setStyleSheet("QGroupBox { color: #6c6; font-weight: bold; }")
        keep_layout = QVBoxLayout(keep_group)
        keep_layout.setContentsMargins(4, 4, 4, 4)
        self.keep_tree = QTreeWidget()
        self.keep_tree.setHeaderLabels(["Species", "CP", "IVs", "IV%", "Reason"])
        self.keep_tree.setAlternatingRowColors(True)
        self.keep_tree.setRootIsDecorated(True)
        keep_layout.addWidget(self.keep_tree)

        move_to_transfer_btn = QPushButton("Move to TRANSFER >>")
        move_to_transfer_btn.clicked.connect(self._move_to_transfer)
        keep_layout.addWidget(move_to_transfer_btn)

        splitter.addWidget(keep_group)

        # Transfer panel
        transfer_group = QGroupBox("TRANSFER")
        transfer_group.setStyleSheet("QGroupBox { color: #c66; font-weight: bold; }")
        transfer_layout = QVBoxLayout(transfer_group)
        transfer_layout.setContentsMargins(4, 4, 4, 4)
        self.transfer_tree = QTreeWidget()
        self.transfer_tree.setHeaderLabels(["Species", "CP", "IVs", "IV%", "Reason"])
        self.transfer_tree.setAlternatingRowColors(True)
        self.transfer_tree.setRootIsDecorated(True)
        transfer_layout.addWidget(self.transfer_tree)

        move_to_keep_btn = QPushButton("<< Move to KEEP")
        move_to_keep_btn.clicked.connect(self._move_to_keep)
        transfer_layout.addWidget(move_to_keep_btn)

        splitter.addWidget(transfer_group)
        splitter.setSizes([500, 500])

        layout.addWidget(splitter, stretch=1)

    def load_pokemon(self, pokemon: list[Pokemon]):
        """Load Pokemon into the keep/transfer trees grouped by species."""
        self._pokemon_map = {p.id: p for p in pokemon}

        keep = [p for p in pokemon if p.decision == "KEEP"]
        transfer = [p for p in pokemon if p.decision == "TRANSFER"]

        self._populate_tree(self.keep_tree, keep)
        self._populate_tree(self.transfer_tree, transfer)

        self.stats_label.setText(
            f"KEEP: {len(keep)} | TRANSFER: {len(transfer)} | Total: {len(pokemon)}"
        )

    def _populate_tree(self, tree: QTreeWidget, pokemon: list[Pokemon]):
        tree.clear()
        # Group by species
        species_groups: dict[str, list[Pokemon]] = {}
        for p in pokemon:
            species_groups.setdefault(p.species, []).append(p)

        for species in sorted(species_groups.keys()):
            group = species_groups[species]
            parent = QTreeWidgetItem(tree, [
                f"{species} ({len(group)})", "", "", "", ""
            ])
            parent.setExpanded(len(group) <= 5)

            for p in sorted(group, key=lambda x: -x.iv_total):
                item = QTreeWidgetItem(parent, [
                    p.species,
                    str(p.cp),
                    f"{p.atk}/{p.def_}/{p.sta}",
                    f"{p.iv_pct:.0%}",
                    p.decision_reason or "",
                ])
                item.setData(0, Qt.UserRole, p.id)

                # Color code
                if p.shiny:
                    for col in range(5):
                        item.setForeground(col, QColor(255, 215, 0))
                if p.shadow:
                    for col in range(5):
                        item.setForeground(col, QColor(180, 100, 220))
                if p.is_dynamax:
                    for col in range(5):
                        item.setForeground(col, QColor(255, 100, 100))

        tree.resizeColumnToContents(0)
        tree.resizeColumnToContents(1)
        tree.resizeColumnToContents(2)

    def _move_to_transfer(self):
        item = self.keep_tree.currentItem()
        if not item or item.parent() is None:
            return  # skip species group headers
        pid = item.data(0, Qt.UserRole)
        if pid and pid in self._pokemon_map:
            self._pokemon_map[pid].decision = "TRANSFER"
            self._pokemon_map[pid].decision_reason = "MANUAL"
            self.decisions_changed.emit()

    def _move_to_keep(self):
        item = self.transfer_tree.currentItem()
        if not item or item.parent() is None:
            return
        pid = item.data(0, Qt.UserRole)
        if pid and pid in self._pokemon_map:
            self._pokemon_map[pid].decision = "KEEP"
            self._pokemon_map[pid].decision_reason = "MANUAL"
            self.decisions_changed.emit()

    def get_enabled_rules(self) -> list[str]:
        """Return list of enabled decision rule IDs."""
        return [rule_id for rule_id, cb in self.rule_checks.items() if cb.isChecked()]

    def get_selected_fav_passes(self) -> list[str]:
        """Return list of selected pass names for favoriting."""
        passes = []
        if self.fav_pass_normal.isChecked():
            passes.append("Normal")
        if self.fav_pass_shiny.isChecked():
            passes.append("Shiny")
        if self.fav_pass_shadow.isChecked():
            passes.append("Shadow")
        if self.fav_pass_dynamax.isChecked():
            passes.append("Dynamax")
        if self.fav_pass_gmax.isChecked():
            passes.append("Gigantamax")
        return passes

    def get_transfer_count(self) -> int:
        return sum(1 for p in self._pokemon_map.values() if p.decision == "TRANSFER")

    def set_favoriting(self, active: bool, dry_run: bool = False, action: str = "Favoriting"):
        """Start or release the initiating action's controls after worker cleanup."""
        self._fav_active = active
        self.fav_progress_bar.setVisible(active)
        self.fav_pause_btn.setVisible(active)
        self.fav_stop_btn.setVisible(active)
        self.fav_log.setVisible(active or bool(self.fav_log.toPlainText()))
        self.approve_btn.setEnabled(not active)
        self.favorite_real_btn.setEnabled(not active)
        self.run_engine_btn.setEnabled(not active)
        self.unfavorite_btn.setEnabled(not active)

        if active:
            self._fav_dry_run = dry_run
            self._fav_action = action
            self._fav_last_error = None
            self._fav_paused_at = None
            self._fav_paused_total = 0.
            self._fav_last_progress = 0
            self._fav_stopping = False
            self.fav_log.clear()
            self.fav_progress_bar.setRange(0, 0)
            self.fav_progress_bar.setValue(0)
            self.fav_progress_bar.setFormat("Starting...")
            self.fav_pause_btn.setText("Pause")
            self.fav_pause_btn.setEnabled(True)
            self.fav_stop_btn.setEnabled(True)
            mode = "DRY RUN" if dry_run else "LIVE"
            self._fav_running_label = f"{action} ({mode})..."
            self.fav_status_label.setText(self._fav_running_label)
            self.fav_status_label.setStyleSheet(
                f"font-weight: bold; color: {'#8cf' if dry_run else '#fc8'};"
            )
            self._fav_start_time = time.monotonic()

    def set_fav_paused(self, paused: bool):
        if not self._fav_active or self._fav_stopping:
            return
        now = time.monotonic()
        if paused and self._fav_paused_at is None:
            self._fav_paused_at = now
        elif not paused and self._fav_paused_at is not None:
            self._fav_paused_total += now - self._fav_paused_at
            self._fav_paused_at = None
        self.fav_pause_btn.setText("Resume" if paused else "Pause")
        self.fav_status_label.setText("Paused" if paused else self._fav_running_label)

    def set_fav_stopping(self):
        if not self._fav_active:
            return
        self._fav_stopping = True
        self.fav_pause_btn.setEnabled(False)
        self.fav_stop_btn.setEnabled(False)
        self.fav_status_label.setText("Stopping...")

    def _append_fav_log(self, message: str):
        escaped = html.escape(str(message)).replace("\n", "<br>")
        self.fav_log.append(f"<p>{escaped}</p>")
        scrollbar = self.fav_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_fav_error(self, message: str):
        if not self._fav_active:
            return
        self._fav_last_error = message
        self.fav_status_label.setText(f"Error: {message}")
        self.fav_status_label.setStyleSheet("font-weight: bold; color: #f88;")
        self._append_fav_log(f"ERROR: {message}")

    def on_fav_progress(self, current: int, total: int, message: str):
        """Update favorite progress. current=checked, total=Pokemon in filter."""
        if not self._fav_active:
            return
        self.fav_progress_bar.setRange(0, total if total > 0 else 0)
        self.fav_progress_bar.setValue(current)

        now = time.monotonic()
        if current < self._fav_last_progress:
            self._fav_start_time, self._fav_paused_total = now, 0.
            if self._fav_paused_at is not None:
                self._fav_paused_at = now
        self._fav_last_progress = current
        active_end = self._fav_paused_at if self._fav_paused_at is not None else now
        elapsed = active_end - self._fav_start_time - self._fav_paused_total
        rate = current / elapsed if elapsed > 0 else 0

        if total > 0 and rate > 0:
            remaining = max(0, (total - current) / rate / 60)
            self.fav_progress_bar.setFormat(
                f"{current}/{total} checked — {rate * 60:.0f}/min — ~{remaining:.0f}min left"
            )
        else:
            self.fav_progress_bar.setFormat(
                f"{current} checked — {rate * 60:.0f}/min"
            )
        self._append_fav_log(message)

    def on_fav_finished(self, result: dict):
        """Keep the actual outcome visible after the worker has released resources."""
        dry = self._fav_dry_run or result.get("dry_run", False)
        labels = {"db_synced": "favorite status saved",
                  "db_unresolved": "favorite status not saved"}
        counts = []
        for key, value in result.items():
            if type(value) is not int or (dry and key in labels):
                continue
            label = labels.get(key, key.replace("_", " "))
            if dry and key in ("favorited", "unfavorited"):
                label = "would favorite" if key == "favorited" else "would unfavorite"
            counts.append(f"{label}: {value}")
        needs_review = any(type(result.get(key)) is int and result[key] > 0
                           for key in ("unmatched", "ambiguous", "unresolved"))
        needs_review = needs_review or (not dry and type(result.get("db_unresolved")) is int
                                        and result["db_unresolved"] > 0)
        if "error" in result and result["error"] is not None:
            error = result["error"] or "The action failed without an error message"
            title = f"{'Dry run failed' if dry else 'Failed'} — {error}"
            color = "#f88"
        elif result.get("aborted"):
            title, color = ("Dry run stopped" if dry else "Stopped"), "#fc8"
        elif needs_review:
            title = "Dry run complete — needs review" if dry else "Completed — needs review"
            color = "#fc8"
        elif self._fav_last_error is not None:
            title = "Dry run complete with errors" if dry else "Completed with errors"
            color = "#fc8"
        elif dry:
            title, color = "Dry run complete", "#8cf"
        else:
            title, color = "Done", "#8f8"
        status = f"{title} — {', '.join(counts)}" if counts else title
        if result.get("note"):
            status += f"\n{result['note']}"
        self.fav_status_label.setText(status)
        self.fav_status_label.setStyleSheet(f"font-weight: bold; color: {color};")
        self._append_fav_log(status)
        self.set_favoriting(False)
        if self.fav_progress_bar.maximum() == 0:
            self.fav_progress_bar.setRange(0, 1)
        self.fav_progress_bar.setFormat(title)
        self.fav_progress_bar.setVisible(True)
        self.fav_status_label.setVisible(True)
        self.fav_log.setVisible(True)
