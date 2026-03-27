"""Collection browser — sortable/filterable table of all scanned Pokemon."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableView, QLineEdit,
    QComboBox, QCheckBox, QLabel, QHeaderView, QPushButton,
)
from PySide6.QtCore import Qt, QSortFilterProxyModel

from ..models.pokemon_table_model import PokemonTableModel
from ...data.models import Pokemon


class CollectionBrowser(QWidget):
    """Table view with sorting and filtering for the Pokemon collection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_pokemon: list[Pokemon] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Filter bar ──
        filter_row = QHBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search species...")
        self.search_input.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self.search_input)

        self.decision_filter = QComboBox()
        self.decision_filter.addItems(["All", "KEEP", "TRANSFER", "Undecided"])
        self.decision_filter.currentTextChanged.connect(self._apply_filters)
        filter_row.addWidget(QLabel("Decision:"))
        filter_row.addWidget(self.decision_filter)

        self.shiny_check = QCheckBox("Shiny")
        self.shiny_check.stateChanged.connect(self._apply_filters)
        filter_row.addWidget(self.shiny_check)

        self.shadow_check = QCheckBox("Shadow")
        self.shadow_check.stateChanged.connect(self._apply_filters)
        filter_row.addWidget(self.shadow_check)

        self.lucky_check = QCheckBox("Lucky")
        self.lucky_check.stateChanged.connect(self._apply_filters)
        filter_row.addWidget(self.lucky_check)

        self.dynamax_check = QCheckBox("Dynamax")
        self.dynamax_check.stateChanged.connect(self._apply_filters)
        filter_row.addWidget(self.dynamax_check)

        filter_row.addStretch()

        self.dedup_btn = QPushButton("Remove Duplicates")
        self.dedup_btn.setToolTip("Remove duplicate Pokemon (same species, CP, IVs, HP)")
        self.dedup_btn.setStyleSheet("QPushButton { padding: 4px 12px; }")
        filter_row.addWidget(self.dedup_btn)

        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setStyleSheet("QPushButton { padding: 4px 12px; }")
        filter_row.addWidget(self.export_btn)

        layout.addLayout(filter_row)

        # ── Stats bar ──
        self.stats_label = QLabel("No data loaded")
        self.stats_label.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(self.stats_label)

        # ── Table ──
        self.model = PokemonTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(44)  # room for sprites
        self.table.setColumnWidth(0, 48)  # sprite column
        self.table.setStyleSheet("""
            QTableView {
                gridline-color: #444;
                font-size: 13px;
            }
            QHeaderView::section {
                background-color: #3a3a50;
                color: #ddd;
                padding: 4px;
                border: 1px solid #555;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.table)

    def load_pokemon(self, pokemon: list[Pokemon]):
        """Load Pokemon data into the table."""
        self._all_pokemon = list(pokemon)
        self._apply_filters()
        self._update_stats()

    def _apply_filters(self):
        """Filter and reload the table based on current filter settings."""
        filtered = self._all_pokemon

        # Species search
        search = self.search_input.text().strip().lower()
        if search:
            filtered = [p for p in filtered if search in p.species.lower()]

        # Decision filter
        dec = self.decision_filter.currentText()
        if dec == "KEEP":
            filtered = [p for p in filtered if p.decision == "KEEP"]
        elif dec == "TRANSFER":
            filtered = [p for p in filtered if p.decision == "TRANSFER"]
        elif dec == "Undecided":
            filtered = [p for p in filtered if not p.decision]

        # Checkbox filters
        if self.shiny_check.isChecked():
            filtered = [p for p in filtered if p.shiny]
        if self.shadow_check.isChecked():
            filtered = [p for p in filtered if p.shadow]
        if self.lucky_check.isChecked():
            filtered = [p for p in filtered if p.lucky]
        if self.dynamax_check.isChecked():
            filtered = [p for p in filtered if p.is_dynamax]

        self.model.set_data(filtered)
        self._update_stats()

    def _update_stats(self):
        total = len(self._all_pokemon)
        showing = self.model.rowCount()
        keep = sum(1 for p in self._all_pokemon if p.decision == "KEEP")
        transfer = sum(1 for p in self._all_pokemon if p.decision == "TRANSFER")
        species = len(set(p.species for p in self._all_pokemon))

        self.stats_label.setText(
            f"Showing {showing} of {total} Pokemon | "
            f"{species} species | "
            f"{keep} KEEP | {transfer} TRANSFER"
        )
