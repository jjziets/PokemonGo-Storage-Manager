# TRACEWEAVER: file-role=keeper-and-cleanup-progress; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
"""Shared keeper pass context; traversal counts never imply whole-action ETA."""

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class KeeperProgress(QWidget):
    """Present structured executor observations without parsing progress logs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.pass_label = QLabel()
        self.pass_label.setStyleSheet("font-weight: bold; color: #8cf;")
        self.next_label = QLabel()
        self.next_label.setStyleSheet("color: #aaa;")
        self.summary_label = QLabel()
        for label in (self.pass_label, self.next_label, self.summary_label):
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.reset()

    def reset(self):
        self.has_progress = False
        self._data = {}
        self._started_at = time.monotonic()
        self._paused_at = None
        self._paused_total = 0.
        self._stopping = False
        self._outcome = None
        for label in (self.pass_label, self.next_label, self.summary_label):
            label.clear()
        self.hide()

    # TRACEWEAVER: entrypoint=KeeperProgress.update_progress; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
    def update_progress(self, payload):
        if self._outcome is not None:
            return
        self._data = dict(payload)
        self.has_progress = True
        self.show()
        self._render()

    def set_paused(self, paused):
        if self._stopping or self._outcome is not None:
            return
        now = time.monotonic()
        if paused and self._paused_at is None:
            self._paused_at = now
        elif not paused and self._paused_at is not None:
            self._paused_total += now - self._paused_at
            self._paused_at = None
        self._render()

    def set_stopping(self):
        self._stopping = True
        self._render()

    def finish(self, result):
        if not self.has_progress:
            return
        self._data["dry_run"] = result.get("dry_run", self._data.get("dry_run", False))
        self._outcome = ("Failed" if "error" in result and result["error"] is not None
                         else "Stopped" if result.get("aborted")
                         else "Dry run complete" if self._data["dry_run"] else "Finished")
        for source, dest in (("checked", "checked_total"), ("favorited", "favorited_total"),
                             ("unfavorited", "unfavorited_total"), ("verified", "verified_total")):
            if type(result.get(source)) is int:
                self._data[dest] = result[source]
        self._render()

    def _render(self):
        if not self.has_progress:
            return
        data = self._data
        index, total = data.get("pass_index", 0), data.get("pass_total", 0)
        completed = data.get("passes_completed", 0)
        if self._outcome is not None:
            title = f"{self._outcome} · {completed}/{total} passes complete"
        elif data.get("stage") in ("pass_complete", "finished"):
            title = f"{completed}/{total} passes complete · {max(0, total - completed)} remaining"
        else:
            title = (f"Pass {index}/{total}: {data.get('pass_name', '')}"
                     f" · {max(0, total - index)} passes after this")
        cleanup_phase = self._cleanup_phase_label()
        if (self._outcome is None and cleanup_phase
                and data.get("stage") in ("starting_batch", "scanning")):
            title += " · " + cleanup_phase
        if self._outcome is None:
            if self._stopping:
                title = "Stopping · " + title
            elif self._paused_at is not None:
                title = "Paused · " + title
        self.pass_label.setText(title)
        following = data.get("selected_passes", [])[index:]
        self.next_label.setText("Up next: " + " → ".join(following) if following else "Final selected pass")
        if self._outcome is not None:
            self.next_label.setText(f"{max(0, total - completed)} passes unfinished" if completed < total else "All selected passes finished")

        unfavorite = data.get("action") == "unfavorite"
        count = data.get("unfavorited_total", 0) if unfavorite else data.get("favorited_total", 0)
        target = data.get("target_total", 0)
        checked = data.get("checked_total", 0)
        verb = "unfavorite" if unfavorite else "favorite"
        subjects = "cleanup targets" if unfavorite else "target keepers"
        tally = (f"would {verb} {count}/{target} {subjects}" if data.get("dry_run")
                 else f"{count}/{target} {subjects} {verb}d")
        summary = f"Overall: {tally} · {data.get('pending_total', 0)} pending"
        if not (unfavorite and data.get("dry_run")):
            summary += f" · {checked} {'action checks' if unfavorite else 'checks total'}"
        if unfavorite and "verified_total" in data:
            summary += f" · {data['verified_total']} cards verified"
        ambiguous = data.get("ambiguous_total", 0)
        if ambiguous:
            summary += f" · {ambiguous} records need review"
        now = self._paused_at if self._paused_at is not None else time.monotonic()
        elapsed = now - self._started_at - self._paused_total
        if checked and elapsed > 0 and not (unfavorite and data.get("dry_run")):
            summary += (f" · {checked / elapsed * 60:.0f} "
                        f"{'action checks' if unfavorite else 'checks'}/min overall")
        self.summary_label.setText(summary)

    def _cleanup_phase_label(self):
        if self._data.get("action") != "unfavorite":
            return ""
        phase = self._data.get("cleanup_phase")
        if phase == "verify":
            return "Dry run: verify group" if self._data.get("dry_run") else "Step 1/2: verify group"
        return {"unfavorite": "Step 2/2: unfavorite matches",
                "dry_run": "Dry run: verify group"}.get(phase, "")

    def render_traversal(self, bar):
        """Only the independently counted current traversal sets this bar."""
        data = self._data
        stage = data.get("stage", "")
        batch, traversal = data.get("batch_index", 0), data.get("traversal_index", 0)
        prefix = f"Batch {batch} · round {traversal}"
        cleanup_phase = self._cleanup_phase_label()
        if cleanup_phase:
            prefix = f"Group {batch} · {cleanup_phase}"
        current, total = data.get("current", 0), data.get("total", 0)
        if stage in ("starting_pass", "starting_batch", "refreshing"):
            bar.setRange(0, 0)
            bar.setFormat("Refreshing remaining results" if stage == "refreshing"
                          else f"Preparing {prefix}" if cleanup_phase else "Preparing pass results")
        else:
            bar.setRange(0, max(1, total))
            bar.setValue(min(current, total))
            if total > 0:
                visit = "verified" if cleanup_phase and data.get("cleanup_phase") in ("verify", "dry_run") else "checked"
                text = f"{prefix} — {current}/{total} {visit} this round"
            elif stage == "pass_complete" and batch == 0:
                text = ("No pending cleanup targets in this pass" if data.get("action") == "unfavorite"
                        else "No pending keeper targets in this pass")
            else:
                text = {"scanning": "No Pokémon in this round", "error": "Round held",
                        "stopped": "Stopped", "finished": "Selected passes finished",
                        "pass_complete": "Pass complete"}.get(stage, "Waiting for result count")
            bar.setFormat(text)
