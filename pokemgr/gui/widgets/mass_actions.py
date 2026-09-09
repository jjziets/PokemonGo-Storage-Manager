"""Mass actions panel — bulk operations on Pokemon storage."""

import time
import logging
import html
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton,
    QLabel, QProgressBar, QTextEdit, QCheckBox, QGridLayout,
)
from PySide6.QtCore import Qt, Signal

log = logging.getLogger(__name__)


class MassActions(QWidget):
    """Bulk operations: unfavorite all, favorite by category, etc."""

    # Signals for main window to connect
    unfavorite_all = Signal()
    favorite_filter = Signal(str, str)  # (filter_query, label)
    stop_action = Signal()
    pause_action = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Unfavorite section ──
        unfav_group = QGroupBox("Unfavorite")
        unfav_group.setStyleSheet("QGroupBox { color: #c88; font-weight: bold; }")
        unfav_layout = QHBoxLayout(unfav_group)
        unfav_layout.setContentsMargins(8, 8, 8, 8)

        self.unfav_all_btn = QPushButton("Unfavorite ALL Pokemon")
        self.unfav_all_btn.setStyleSheet(
            "QPushButton { background-color: #5a3a3a; padding: 8px 16px; font-weight: bold; }"
        )
        self.unfav_all_btn.setToolTip("Swipe through ALL Pokemon and remove favorite star")
        unfav_layout.addWidget(self.unfav_all_btn)

        unfav_layout.addStretch()

        unfav_desc = QLabel("Removes star from every Pokemon. Run before scanning to start clean.")
        unfav_desc.setStyleSheet("color: #888;")
        unfav_layout.addWidget(unfav_desc)

        layout.addWidget(unfav_group)

        # ── Favorite by category ──
        fav_group = QGroupBox("Favorite by Category")
        fav_group.setStyleSheet("QGroupBox { color: #8c8; font-weight: bold; }")
        fav_grid = QGridLayout(fav_group)
        fav_grid.setContentsMargins(8, 8, 8, 8)
        fav_grid.setSpacing(6)

        # Each button sends a Pokemon Go search filter to the executor
        categories = [
            ("Shiny",      "shiny",              "#ffd700", "Favorite all shiny Pokemon"),
            ("Shadow",     "shadow",             "#b464dc", "Favorite all shadow Pokemon"),
            ("Legendary",  "legendary",          "#f08030", "Favorite all legendary Pokemon"),
            ("Mythical",   "mythical",           "#e868c8", "Favorite all mythical Pokemon"),
            ("Ultra Beast","ultra beast",         "#70d0e0", "Favorite all Ultra Beasts"),
            ("Lucky",      "lucky",              "#f8d030", "Favorite all lucky Pokemon"),
            ("4*",         "4*",                 "#a0f0a0", "Favorite all 100% IV Pokemon"),
            ("3*",         "3*",                 "#80c0f0", "Favorite all 3-star Pokemon (82-98% IV)"),
            ("Dynamax",    "dynamax",            "#ff6464", "Favorite all Dynamax Pokemon"),
            ("Gigantamax", "gigantamax",         "#ff9090", "Favorite all Gigantamax Pokemon"),
            ("Costume",    "costume",            "#c8a0e0", "Favorite all costume Pokemon"),
            ("Purified",   "purified",           "#f0f0ff", "Favorite all purified Pokemon"),
        ]

        self._cat_buttons = []
        for i, (label, query, color, tooltip) in enumerate(categories):
            btn = QPushButton(f"Fav {label}")
            btn.setStyleSheet(
                f"QPushButton {{ background-color: #2a3a2a; color: {color}; "
                f"padding: 6px 12px; font-weight: bold; }}"
            )
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda checked, q=query, l=label: self.favorite_filter.emit(q, l))
            self._cat_buttons.append(btn)

            row, col = divmod(i, 4)
            fav_grid.addWidget(btn, row, col)

        layout.addWidget(fav_group)

        # ── Favorite KEEP decisions ──
        keep_group = QGroupBox("Favorite by Decision")
        keep_group.setStyleSheet("QGroupBox { color: #8c8; font-weight: bold; }")
        keep_layout = QHBoxLayout(keep_group)
        keep_layout.setContentsMargins(8, 8, 8, 8)

        self.fav_keepers_dry_btn = QPushButton("Fav Keepers (Dry Run)")
        self.fav_keepers_dry_btn.setStyleSheet(
            "QPushButton { background-color: #4a5a2a; padding: 8px 16px; font-weight: bold; }"
        )
        keep_layout.addWidget(self.fav_keepers_dry_btn)

        self.fav_keepers_real_btn = QPushButton("Fav Keepers (FOR REAL)")
        self.fav_keepers_real_btn.setStyleSheet(
            "QPushButton { background-color: #7a6a2a; padding: 8px 16px; font-weight: bold; }"
        )
        keep_layout.addWidget(self.fav_keepers_real_btn)

        keep_layout.addStretch()

        keep_desc = QLabel("Matches validated species/form, CP, HP and IVs; names may be nicknames.")
        keep_desc.setWordWrap(True)
        keep_desc.setStyleSheet("color: #888;")
        keep_layout.addWidget(keep_desc)

        layout.addWidget(keep_group)

        # ── Progress section ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setMaximumHeight(20)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        ctrl_layout = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-weight: bold; color: #fc8;")
        ctrl_layout.addWidget(self.status_label)
        ctrl_layout.addStretch()

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setVisible(False)
        self.pause_btn.clicked.connect(self.pause_action.emit)
        ctrl_layout.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setStyleSheet("QPushButton { background-color: #7a2a2a; }")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self.stop_action.emit)
        ctrl_layout.addWidget(self.stop_btn)

        layout.addLayout(ctrl_layout)

        self.action_log = QTextEdit()
        self.action_log.setReadOnly(True)
        self.action_log.setMaximumHeight(150)
        self.action_log.setVisible(False)
        self.action_log.setStyleSheet(
            "QTextEdit { font-family: monospace; font-size: 11px; background-color: #1a1a2a; color: #ccc; }"
        )
        layout.addWidget(self.action_log)

        layout.addStretch()

    def set_running(self, active: bool, label: str = ""):
        """Show/hide progress controls."""
        self.progress_bar.setVisible(active)
        self.pause_btn.setVisible(active)
        self.stop_btn.setVisible(active)
        self.action_log.setVisible(active)

        # Disable all action buttons while running
        self.unfav_all_btn.setEnabled(not active)
        self.fav_keepers_dry_btn.setEnabled(not active)
        self.fav_keepers_real_btn.setEnabled(not active)
        for btn in self._cat_buttons:
            btn.setEnabled(not active)

        if active:
            self._last_error = ""
            self._running_label = label
            self._paused_at = None
            self._paused_total = 0.
            self._last_progress = 0
            self.action_log.clear()
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setValue(0)
            self.pause_btn.setText("Pause")
            self.pause_btn.setEnabled(True)
            self.stop_btn.setEnabled(True)
            self.status_label.setText(label)
            self.status_label.setStyleSheet("font-weight: bold; color: #fc8;")
            self._start_time = time.monotonic()

    def set_paused(self, paused: bool):
        now = time.monotonic()
        if paused and self._paused_at is None:
            self._paused_at = now
        elif not paused and self._paused_at is not None:
            self._paused_total += now - self._paused_at
            self._paused_at = None
        self.pause_btn.setText("Resume" if paused else "Pause")
        self.status_label.setText("Paused" if paused else self._running_label)

    def set_stopping(self):
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Stopping...")

    def on_error(self, message: str):
        self._last_error = message
        self.status_label.setText(f"Error: {message}")
        self.status_label.setStyleSheet("font-weight: bold; color: #f88;")
        self.action_log.append(html.escape(f"ERROR: {message}"))

    def on_progress(self, current: int, total: int, message: str):
        self.progress_bar.setRange(0, total if total > 0 else 0)
        self.progress_bar.setValue(current)

        now = time.monotonic()
        if current < self._last_progress:
            self._start_time, self._paused_total = now, 0.
            if self._paused_at is not None:
                self._paused_at = now
        self._last_progress = current
        active_end = self._paused_at if self._paused_at is not None else now
        elapsed = active_end - self._start_time - self._paused_total
        rate = current / elapsed if elapsed > 0 else 0

        if total > 0 and rate > 0:
            remaining = max(0, (total - current) / rate / 60)
            self.progress_bar.setFormat(
                f"{current}/{total} — {rate * 60:.0f}/min — ~{remaining:.0f}min left"
            )
        else:
            self.progress_bar.setFormat(f"{current} — {rate * 60:.0f}/min")

        self.action_log.append(html.escape(message))
        scrollbar = self.action_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_finished(self, result: dict):
        msg = ", ".join(f"{('would favorite' if k == 'favorited' and result.get('dry_run') else k)}: {v}"
                        for k, v in result.items() if type(v) is int)
        needs_review = any(type(result.get(key)) is int and result[key] > 0
                           for key in ('unmatched', 'ambiguous', 'unresolved'))
        if result.get('error'):
            title, color = f"Failed — {result['error']}", "#f88"
        elif result.get('aborted'):
            title, color = "Stopped", "#fc8"
        elif needs_review:
            title = "Dry run complete — needs review" if result.get('dry_run') else "Completed — needs review"
            color = "#fc8"
        elif result.get('dry_run'):
            title, color = ("Dry run complete with errors", "#fc8") if getattr(self, '_last_error', '') else ("Dry run complete", "#8cf")
        elif getattr(self, '_last_error', ''):
            title, color = "Completed with errors", "#fc8"
        else:
            title, color = "Done", "#8f8"
        status = f"{title} — {msg}" if msg else title
        if result.get('note'):
            status += f"\n{result['note']}"
        self.status_label.setText(status)
        self.status_label.setStyleSheet(f"font-weight: bold; color: {color};")
        self.action_log.append(html.escape(self.status_label.text()))
        self.set_running(False)
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 1)
        self.progress_bar.setFormat(title)
        # Keep log visible
        self.progress_bar.setVisible(True)
        self.status_label.setVisible(True)
        self.action_log.setVisible(True)
