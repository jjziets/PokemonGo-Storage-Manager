"""Scan control panel — start/stop scanning with live progress."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QProgressBar, QTextEdit, QSpinBox, QDoubleSpinBox, QCheckBox,
    QGroupBox, QLineEdit,
)
from PySide6.QtCore import Qt, Slot

from ...reader.screen import PokemonRead


class ScanControl(QWidget):
    """Controls for starting, pausing, and monitoring the scan."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Connection status ──
        conn_group = QGroupBox("Connection")
        conn_layout = QHBoxLayout(conn_group)
        self.device_label = QLabel("Not connected")
        self.device_label.setStyleSheet("font-size: 14px;")
        conn_layout.addWidget(self.device_label)
        self.connect_btn = QPushButton("Connect")
        conn_layout.addWidget(self.connect_btn)

        self.clear_db_btn = QPushButton("Clear Database")
        self.clear_db_btn.setStyleSheet("QPushButton { background-color: #5a3a3a; }")
        self.clear_db_btn.setToolTip("Delete all scanned data and start fresh")
        conn_layout.addWidget(self.clear_db_btn)
        layout.addWidget(conn_group)

        # ── Scan settings ──
        settings_group = QGroupBox("Scan Settings")
        settings_layout = QHBoxLayout(settings_group)

        settings_layout.addWidget(QLabel("Max per pass:"))
        self.max_pokemon_spin = QSpinBox()
        self.max_pokemon_spin.setRange(0, 10000)
        self.max_pokemon_spin.setValue(0)
        self.max_pokemon_spin.setSpecialValueText("All (until wrap-around)")
        settings_layout.addWidget(self.max_pokemon_spin)

        self.unfavorite_check = QCheckBox("Unfavorite during scan")
        self.unfavorite_check.setToolTip("Remove favorite star from all Pokemon while scanning")
        settings_layout.addWidget(self.unfavorite_check)

        settings_layout.addWidget(QLabel("Skip first:"))
        self.skip_spin = QSpinBox()
        self.skip_spin.setRange(0, 10000)
        self.skip_spin.setValue(0)
        self.skip_spin.setToolTip("Skip this many Pokemon before scanning (for resume)")
        settings_layout.addWidget(self.skip_spin)

        settings_layout.addWidget(QLabel("Skip delay:"))
        self.skip_delay_spin = QDoubleSpinBox()
        self.skip_delay_spin.setRange(0.01, 1.0)
        self.skip_delay_spin.setValue(0.05)
        self.skip_delay_spin.setSingleStep(0.01)
        self.skip_delay_spin.setSuffix("s")
        self.skip_delay_spin.setToolTip("Delay between swipes when skipping (lower=faster, may miss)")
        settings_layout.addWidget(self.skip_delay_spin)

        settings_layout.addStretch()
        layout.addWidget(settings_group)

        # ── Resume target (verify after skip) ──
        resume_group = QGroupBox("Resume Target (optional — verify after skip)")
        resume_layout = QHBoxLayout(resume_group)
        resume_layout.addWidget(QLabel("Expected species:"))
        self.resume_species = QLineEdit()
        self.resume_species.setPlaceholderText("e.g. Wooper")
        self.resume_species.setMaximumWidth(150)
        resume_layout.addWidget(self.resume_species)
        resume_layout.addWidget(QLabel("Expected CP:"))
        self.resume_cp = QSpinBox()
        self.resume_cp.setRange(0, 10000)
        self.resume_cp.setValue(0)
        self.resume_cp.setSpecialValueText("any")
        self.resume_cp.setMaximumWidth(80)
        resume_layout.addWidget(self.resume_cp)
        resume_layout.addStretch()
        layout.addWidget(resume_group)

        # ── Speed settings ──
        speed_group = QGroupBox("Speed Tuning")
        speed_layout = QHBoxLayout(speed_group)

        speed_layout.addWidget(QLabel("Appraise delay:"))
        self.appraise_delay_spin = QDoubleSpinBox()
        self.appraise_delay_spin.setRange(0.1, 3.0)
        self.appraise_delay_spin.setValue(0.5)
        self.appraise_delay_spin.setSingleStep(0.1)
        self.appraise_delay_spin.setSuffix("s")
        self.appraise_delay_spin.setToolTip("Wait after opening appraisal (lower = faster, may miss bars)")
        speed_layout.addWidget(self.appraise_delay_spin)

        speed_layout.addWidget(QLabel("Swipe delay:"))
        self.swipe_delay_spin = QDoubleSpinBox()
        self.swipe_delay_spin.setRange(0.0, 1.0)
        self.swipe_delay_spin.setValue(0.0)
        self.swipe_delay_spin.setSingleStep(0.05)
        self.swipe_delay_spin.setSuffix("s")
        self.swipe_delay_spin.setToolTip("Wait after swipe before reading (lower = faster)")
        speed_layout.addWidget(self.swipe_delay_spin)

        speed_layout.addWidget(QLabel("Bar wait:"))
        self.bar_wait_spin = QDoubleSpinBox()
        self.bar_wait_spin.setRange(0.1, 3.0)
        self.bar_wait_spin.setValue(1.5)
        self.bar_wait_spin.setSingleStep(0.1)
        self.bar_wait_spin.setSuffix("s")
        self.bar_wait_spin.setToolTip("Max time to wait for appraisal bars to appear (lower = faster, may miss)")
        speed_layout.addWidget(self.bar_wait_spin)

        speed_layout.addWidget(QLabel("Swipe ms:"))
        self.swipe_duration_spin = QSpinBox()
        self.swipe_duration_spin.setRange(50, 500)
        self.swipe_duration_spin.setValue(120)
        self.swipe_duration_spin.setSingleStep(10)
        self.swipe_duration_spin.setToolTip("Swipe gesture duration in milliseconds (lower = faster flick)")
        speed_layout.addWidget(self.swipe_duration_spin)

        self.calc_cp_check = QCheckBox("Calc CP")
        self.calc_cp_check.setChecked(False)
        self.calc_cp_check.setToolTip("Use calculated CP from IVs+HP+Species instead of OCR (faster, avoids CP read issues)")
        speed_layout.addWidget(self.calc_cp_check)

        self.anti_detection_check = QCheckBox("Anti-detect")
        self.anti_detection_check.setChecked(True)
        self.anti_detection_check.setToolTip("Random micro-breaks every 50-120 Pokemon")
        speed_layout.addWidget(self.anti_detection_check)

        self.size_tags_check = QCheckBox("Size tags")
        self.size_tags_check.setChecked(False)
        self.size_tags_check.setToolTip(
            "Capture LIGHTEST/HEAVIEST/SHORTEST/TALLEST tags.\n"
            "Closes appraisal briefly per Pokemon (~2s slower each).\n"
            "Off by default for speed."
        )
        speed_layout.addWidget(self.size_tags_check)

        self.save_speed_btn = QPushButton("Save")
        self.save_speed_btn.setToolTip("Save current speed settings")
        self.save_speed_btn.clicked.connect(self._save_speed_settings)
        speed_layout.addWidget(self.save_speed_btn)

        self.reset_speed_btn = QPushButton("Reset")
        self.reset_speed_btn.setToolTip("Reset speed settings to defaults")
        self.reset_speed_btn.clicked.connect(self._reset_speed_settings)
        speed_layout.addWidget(self.reset_speed_btn)

        speed_layout.addStretch()
        layout.addWidget(speed_group)

        # Load saved settings
        self._load_speed_settings()

        # ── Scan passes ──
        passes_group = QGroupBox("Scan Passes")
        passes_layout = QVBoxLayout(passes_group)

        self.pass_normal = QCheckBox("Normal (excludes all checked categories below)")
        self.pass_normal.setChecked(True)
        self.pass_normal.setToolTip("Scans everything NOT in the other selected categories")
        passes_layout.addWidget(self.pass_normal)

        self.pass_shiny = QCheckBox("Shiny")
        self.pass_shiny.setChecked(True)
        self.pass_shiny.setToolTip("Scan shiny Pokemon (tagged automatically)")
        passes_layout.addWidget(self.pass_shiny)

        self.pass_shadow = QCheckBox("Shadow")
        self.pass_shadow.setChecked(True)
        self.pass_shadow.setToolTip("Scan shadow Pokemon (tagged automatically)")
        passes_layout.addWidget(self.pass_shadow)

        self.pass_lucky = QCheckBox("Lucky")
        self.pass_lucky.setChecked(False)
        self.pass_lucky.setToolTip("Scan lucky Pokemon separately (tagged automatically)")
        passes_layout.addWidget(self.pass_lucky)

        self.pass_dynamax = QCheckBox("Dynamax")
        self.pass_dynamax.setChecked(False)
        self.pass_dynamax.setToolTip("Scan dynamax Pokemon separately (also detected by icon)")
        passes_layout.addWidget(self.pass_dynamax)

        self.pass_gigantamax = QCheckBox("Gigantamax")
        self.pass_gigantamax.setChecked(False)
        self.pass_gigantamax.setToolTip("Scan gigantamax Pokemon separately (also detected by icon)")
        passes_layout.addWidget(self.pass_gigantamax)

        self.pass_custom = QCheckBox("Custom filter:")
        self.pass_custom.setChecked(False)
        custom_row = QHBoxLayout()
        custom_row.addWidget(self.pass_custom)
        self.custom_filter_input = QLineEdit()
        self.custom_filter_input.setPlaceholderText("e.g. legendary, 4*, mythical")
        self.custom_filter_input.setEnabled(False)
        self.pass_custom.stateChanged.connect(
            lambda state: self.custom_filter_input.setEnabled(state == 2)
        )
        custom_row.addWidget(self.custom_filter_input)
        passes_layout.addLayout(custom_row)

        layout.addWidget(passes_group)

        # ── Scan controls ──
        btn_layout = QHBoxLayout()

        self.start_full_btn = QPushButton("Multi-Pass Scan")
        self.start_full_btn.setToolTip(
            "Full scan: filters by !shiny, shiny, shadow, dynamax, gigantamax\n"
            "Navigates automatically from game map"
        )
        self.start_full_btn.setStyleSheet(
            "QPushButton { background-color: #2a7a2a; padding: 10px 20px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background-color: #3a9a3a; }"
        )
        btn_layout.addWidget(self.start_full_btn)

        self.start_current_btn = QPushButton("Scan from Here")
        self.start_current_btn.setToolTip(
            "Start scanning from your current position on the phone.\n"
            "Navigate to the Pokemon you want to start from, then click this.\n"
            "Works from detail screen or appraisal screen."
        )
        self.start_current_btn.setStyleSheet(
            "QPushButton { background-color: #2a5a7a; padding: 10px 20px; font-size: 14px; }"
        )
        btn_layout.addWidget(self.start_current_btn)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setEnabled(False)
        self.pause_btn.setStyleSheet("QPushButton { padding: 10px 15px; }")
        btn_layout.addWidget(self.pause_btn)

        self.abort_btn = QPushButton("Stop")
        self.abort_btn.setEnabled(False)
        self.abort_btn.setStyleSheet(
            "QPushButton { background-color: #7a2a2a; padding: 10px 15px; }"
            "QPushButton:hover { background-color: #9a3a3a; }"
        )
        btn_layout.addWidget(self.abort_btn)

        layout.addLayout(btn_layout)

        # ── Progress ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("0 Pokemon scanned")
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(10000)  # will update with actual count
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        # ── Status ──
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #8cf;")
        layout.addWidget(self.status_label)

        # ── Live log ──
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(400)
        self.log_view.setStyleSheet(
            "QTextEdit { font-family: monospace; font-size: 12px; background-color: #1a1a2a; color: #ccc; }"
        )
        layout.addWidget(self.log_view)

        layout.addStretch()

    def set_scanning(self, active: bool):
        """Toggle UI state between scanning and idle."""
        if active:
            import time
            self._scan_start_time = time.time()
            self._last_total = 0
            self._pass_start_count = 0
            self._pass_expected = 0
            self._retry_count = 0
            self._cp_fail_count = 0
            self._skipped_count = 0
        self.start_full_btn.setEnabled(not active)
        self.start_current_btn.setEnabled(not active)
        self.pause_btn.setEnabled(active)
        self.abort_btn.setEnabled(active)
        self.max_pokemon_spin.setEnabled(not active)
        self.unfavorite_check.setEnabled(not active)

    @Slot(int)
    def on_pass_count(self, expected: int):
        """Update progress bar max when we know how many Pokemon are in this pass."""
        max_per_pass = self.max_pokemon_spin.value()

        # Only trust OCR count if it's reasonable (> max_per_pass means it's real)
        # If OCR reads less than max_per_pass, it's likely a misread
        if max_per_pass > 0 and expected > max_per_pass:
            # OCR found more than our limit — use the limit
            self._pass_expected = max_per_pass
        elif max_per_pass > 0:
            # OCR unreliable or less than limit — just use the limit
            self._pass_expected = max_per_pass
        elif expected > 10:
            # No limit set, OCR looks reasonable
            self._pass_expected = expected
        else:
            # No limit, OCR unreliable
            self._pass_expected = 0

        self._pass_start_count = getattr(self, '_last_total', 0)
        self.progress_bar.setMaximum(self._pass_expected if self._pass_expected > 0 else 10000)
        self.progress_bar.setValue(0)
        import time
        self._pass_start_time = time.time()

    @Slot(int, object)
    def on_progress(self, total_count: int, pokemon: PokemonRead):
        self._last_total = total_count

        # Current pass progress
        pass_start = getattr(self, '_pass_start_count', 0)
        pass_count = total_count - pass_start
        self.progress_bar.setValue(pass_count)

        import time
        pass_start_time = getattr(self, '_pass_start_time', None)
        if pass_start_time is None:
            pass_start_time = self._scan_start_time
        elapsed = time.time() - pass_start_time
        rate = pass_count / elapsed if elapsed > 0 else 0

        # Track retries and CP fails
        if not hasattr(self, '_retry_count'):
            self._retry_count = 0
            self._cp_fail_count = 0
        if pokemon.cp == -1:
            self._cp_fail_count += 1
        if pokemon.atk == 0 and pokemon.def_ == 0 and pokemon.sta == 0:
            self._retry_count += 1

        issues = ""
        if self._retry_count > 0 or self._cp_fail_count > 0 or self._skipped_count > 0:
            parts = []
            if self._cp_fail_count > 0:
                parts.append(f"cp_fail:{self._cp_fail_count}")
            if self._skipped_count > 0:
                parts.append(f"skipped:{self._skipped_count}")
            if self._retry_count > 0:
                parts.append(f"iv_zero:{self._retry_count}")
            issues = f" | {' '.join(parts)}"

        expected = getattr(self, '_pass_expected', 0)
        if expected > 0 and rate > 0 and pass_count <= expected:
            remaining = max(0, (expected - pass_count) / rate / 60)
            self.progress_bar.setFormat(
                f"{pass_count}/{expected} — {rate * 60:.0f}/min — ~{remaining:.0f}min left{issues}"
            )
        else:
            self.progress_bar.setFormat(
                f"{pass_count} scanned — {rate * 60:.0f}/min (total: {total_count}){issues}"
            )
        # Show current pass type as a tag
        pass_name = getattr(self, '_current_pass_name', 'Normal')
        pass_colors = {
            'Shiny': ('gold', 'black'),
            'Shadow': ('#b464dc', 'white'),
            'Dynamax': ('#ff6464', 'white'),
            'Gigantamax': ('#ff6464', 'white'),
            'Lucky': ('#6c6', 'black'),
        }
        pass_tag = ""
        if pass_name in pass_colors:
            bg, fg = pass_colors[pass_name]
            pass_tag = f' <span style="background-color: {bg}; color: {fg}; padding: 1px 4px; border-radius: 3px;">{pass_name}</span>'

        gender_str = ""
        if pokemon.gender == "male":
            gender_str = ' <span style="color: #6af;">♂</span>'
        elif pokemon.gender == "female":
            gender_str = ' <span style="color: #f6a;">♀</span>'

        self.log_view.append(
            f'<span style="color: #888;">#{total_count}</span> '
            f'<b>{pokemon.species}</b> CP{pokemon.cp} '
            f'{pokemon.atk}/{pokemon.def_}/{pokemon.sta} '
            f'({pokemon.iv_pct:.0%}){gender_str}{pass_tag}'
        )
        # Auto-scroll
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @Slot(str)
    def on_status(self, msg: str):
        self.status_label.setText(msg)
        # Track current pass name and add separator to log
        if "Pass" in msg:
            try:
                self._current_pass_name = msg.split(":")[1].strip().split("(")[0].strip()
                self.log_view.append(
                    f'<br><span style="color: #8cf; font-weight: bold;">══ {msg} ══</span>'
                )
            except (IndexError, AttributeError):
                pass

    @Slot(str)
    def on_error(self, msg: str):
        if "Skipped" in msg:
            self._skipped_count = getattr(self, '_skipped_count', 0) + 1
        self.log_view.append(f'<span style="color: #f66;">{msg}</span>')
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ── Speed settings persistence ─────────────────────────────────

    SPEED_DEFAULTS = {
        "appraise_delay": 0.5,
        "swipe_delay": 0.0,
        "bar_wait": 1.5,
        "swipe_ms": 120,
        "anti_detection": True,
        "calc_cp": False,
        "size_tags": False,
    }

    def _save_speed_settings(self):
        import json
        from ...config import DATA_DIR
        settings = {
            "appraise_delay": self.appraise_delay_spin.value(),
            "swipe_delay": self.swipe_delay_spin.value(),
            "bar_wait": self.bar_wait_spin.value(),
            "swipe_ms": self.swipe_duration_spin.value(),
            "anti_detection": self.anti_detection_check.isChecked(),
            "calc_cp": self.calc_cp_check.isChecked(),
            "size_tags": self.size_tags_check.isChecked(),
        }
        path = DATA_DIR / "speed_settings.json"
        path.write_text(json.dumps(settings, indent=2))

    def _load_speed_settings(self):
        import json
        from ...config import DATA_DIR
        path = DATA_DIR / "speed_settings.json"
        try:
            if path.exists():
                settings = json.loads(path.read_text())
                self.appraise_delay_spin.setValue(settings.get("appraise_delay", self.SPEED_DEFAULTS["appraise_delay"]))
                self.swipe_delay_spin.setValue(settings.get("swipe_delay", self.SPEED_DEFAULTS["swipe_delay"]))
                self.bar_wait_spin.setValue(settings.get("bar_wait", self.SPEED_DEFAULTS["bar_wait"]))
                self.swipe_duration_spin.setValue(settings.get("swipe_ms", self.SPEED_DEFAULTS["swipe_ms"]))
                self.anti_detection_check.setChecked(settings.get("anti_detection", self.SPEED_DEFAULTS["anti_detection"]))
                self.calc_cp_check.setChecked(settings.get("calc_cp", self.SPEED_DEFAULTS["calc_cp"]))
                self.size_tags_check.setChecked(settings.get("size_tags", self.SPEED_DEFAULTS["size_tags"]))
        except Exception:
            pass

    def _reset_speed_settings(self):
        self.appraise_delay_spin.setValue(self.SPEED_DEFAULTS["appraise_delay"])
        self.swipe_delay_spin.setValue(self.SPEED_DEFAULTS["swipe_delay"])
        self.bar_wait_spin.setValue(self.SPEED_DEFAULTS["bar_wait"])
        self.swipe_duration_spin.setValue(self.SPEED_DEFAULTS["swipe_ms"])
        self.anti_detection_check.setChecked(self.SPEED_DEFAULTS["anti_detection"])
        self.calc_cp_check.setChecked(self.SPEED_DEFAULTS["calc_cp"])
        self.size_tags_check.setChecked(self.SPEED_DEFAULTS["size_tags"])
        # Delete saved file
        from ...config import DATA_DIR
        path = DATA_DIR / "speed_settings.json"
        if path.exists():
            path.unlink()

    def get_selected_passes(self) -> list[dict]:
        """Return the list of scan passes based on checked boxes.

        The Normal pass automatically excludes all other selected categories.
        """
        # Collect all the category passes first
        category_passes = []
        exclude_filters = []  # for building the Normal filter

        if self.pass_shiny.isChecked():
            category_passes.append({"name": "Shiny", "query": "shiny", "tags": {"shiny": True}})
            exclude_filters.append("!shiny")
        if self.pass_shadow.isChecked():
            category_passes.append({"name": "Shadow", "query": "shadow", "tags": {"shadow": True}})
            exclude_filters.append("!shadow")
        if self.pass_lucky.isChecked():
            category_passes.append({"name": "Lucky", "query": "lucky", "tags": {"lucky": True}})
            exclude_filters.append("!lucky")
        if self.pass_dynamax.isChecked():
            category_passes.append({"name": "Dynamax", "query": "dynamax", "tags": {"is_dynamax": True}})
            exclude_filters.append("!dynamax")
        if self.pass_gigantamax.isChecked():
            category_passes.append({"name": "Gigantamax", "query": "gigantamax", "tags": {"is_dynamax": True}})
            exclude_filters.append("!gigantamax")

        passes = []

        # Normal pass: exclude everything that has its own pass
        if self.pass_normal.isChecked():
            if exclude_filters:
                normal_query = "&".join(exclude_filters)
            else:
                normal_query = ""  # no filter = scan everything
            passes.append({"name": "Normal", "query": normal_query, "tags": {}})

        # Add category passes after Normal
        passes.extend(category_passes)

        # Custom filter at the end
        if self.pass_custom.isChecked() and self.custom_filter_input.text().strip():
            passes.append({
                "name": "Custom",
                "query": self.custom_filter_input.text().strip(),
                "tags": {},
            })

        return passes

    @Slot(int)
    def on_finished(self, count: int):
        self.set_scanning(False)
        skipped = getattr(self, '_skipped_count', 0)
        cp_fails = getattr(self, '_cp_fail_count', 0)
        summary = f"Scan complete: {count} Pokemon saved"
        if skipped > 0:
            summary += f", {skipped} skipped (favorited for protection)"
        if cp_fails > 0:
            summary += f", {cp_fails} CP read failures"
        self.status_label.setText(summary)
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #8f8;")
