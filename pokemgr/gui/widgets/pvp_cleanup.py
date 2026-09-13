# TRACEWEAVER: file-role=reviewed-pvp-cleanup-preview; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
"""Review the exact stored occurrences authorized for selective unfavoriting."""

from collections import defaultdict
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from ...execution.pvp_cleanup import cleanup_group_key


class PvpCleanupDialog(QDialog):
    # TRACEWEAVER: entrypoint=PvpCleanupDialog.__init__; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review PvP cleanup")
        self.resize(860, 560)
        self.dry_run = True
        self._rows = {p.id: replace(p) for p in plan.candidates}
        self._items_by_group = defaultdict(list)
        self._group_by_id = {
            p.id: cleanup_group_key((p.species, p.cp, p.hp, p.atk, p.def_, p.sta,
                                    p.shiny, p.shadow, p.lucky, p.is_dynamax))
            for p in self._rows.values()
        }
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Unfavorite selected 0–2★ Pokémon marked TRANSFER by your saved decisions.\n"
            "Every KEEP and every 3–4★ Pokémon stays protected.\n"
            "Uncheck any personal exceptions. This uses saved decisions; review your PvP keepers first.\n"
            "Matching Pokémon are selected together: unchecking one protects the entire group.\n"
            "Use the game account and device these records were scanned from."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        summary = QLabel(
            f"Recorded favorites: {plan.total_favorited:,} · Eligible: {plan.eligible:,}"
            f" · Protected: {plan.protected:,} · Ambiguous: {plan.ambiguous:,}"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Pokémon", "CP", "HP", "IVs", "Appraisal", "Decision"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        for row in sorted(self._rows.values(), key=lambda p: (p.species.casefold(), -p.cp, p.id)):
            item = QTreeWidgetItem(self.tree, [row.species, str(row.cp), str(row.hp),
                f"{row.atk}/{row.def_}/{row.sta}", f"{row.star_rating}★", "TRANSFER"])
            item.setData(0, Qt.UserRole, row.id)
            item.setCheckState(0, Qt.Checked)
            self._items_by_group[self._group_by_id[row.id]].append(item)
        for items in self._items_by_group.values():
            if len(items) > 1:
                for item in items:
                    item.setText(5, f"TRANSFER (group of {len(items)})")
        for column in range(6):
            self.tree.resizeColumnToContents(column)
        layout.addWidget(self.tree, 1)
        selection_bar = QHBoxLayout()
        self.selection_label = QLabel()
        selection_bar.addWidget(self.selection_label)
        selection_bar.addStretch()
        for label, state in (("Select all", Qt.Checked), ("Clear selection", Qt.Unchecked)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=state: self._select_all(value))
            selection_bar.addWidget(button)
        layout.addLayout(selection_bar)
        note = QLabel(
            "The app verifies a group, then swipes through it to unfavorite the selected matches. "
            "Dry run only verifies. Leave the device alone while it runs. "
            "A matching group is unfavorited only when every member is reviewed as unwanted. "
            "Unverified groups remain favorited. No Pokémon are transferred."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch()
        self.dry_button = QPushButton("Dry run selected")
        self.dry_button.setDefault(True)
        self.dry_button.clicked.connect(lambda: self._start(True))
        self.real_button = QPushButton("Unfavorite selected")
        self.real_button.clicked.connect(lambda: self._start(False))
        buttons.addWidget(self.dry_button)
        buttons.addWidget(self.real_button)
        layout.addLayout(buttons)
        self.tree.itemChanged.connect(self._selection_changed)
        self._selection_changed()

    def selected_candidates(self):
        return [replace(self._rows[item.data(0, Qt.UserRole)])
                for index in range(self.tree.topLevelItemCount())
                if (item := self.tree.topLevelItem(index)).checkState(0) == Qt.Checked]

    def _select_all(self, state):
        blocked = self.tree.blockSignals(True)
        try:
            for index in range(self.tree.topLevelItemCount()):
                self.tree.topLevelItem(index).setCheckState(0, state)
        finally:
            self.tree.blockSignals(blocked)
        self._selection_changed()

    def _selection_changed(self, item=None, column=0):
        if item is not None and column == 0:
            group = self._group_by_id[item.data(0, Qt.UserRole)]
            blocked = self.tree.blockSignals(True)
            try:
                for peer in self._items_by_group[group]:
                    peer.setCheckState(0, item.checkState(0))
            finally:
                self.tree.blockSignals(blocked)
        count = len(self.selected_candidates())
        self.selection_label.setText(f"{count:,} selected")
        self.dry_button.setEnabled(count > 0)
        self.real_button.setEnabled(count > 0)

    def _start(self, dry_run):
        if self.selected_candidates():
            self.dry_run = dry_run
            self.accept()
