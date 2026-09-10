# TRACEWEAVER: file-role=inventory-queue-entry; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
# TRACEWEAVER: entrypoint=run_gui; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
"""PySide6 application entry point."""

import sys
import logging
import warnings
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

# Suppress PyTorch/PaddleOCR warnings and logging
warnings.filterwarnings('ignore', message='.*pin_memory.*')
warnings.filterwarnings('ignore', message='.*paddle.*')
import os
os.environ['GLOG_minloglevel'] = '3'
os.environ['FLAGS_log_prefix'] = '0'
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
# Suppress paddle model loading messages
import logging as _logging
for name in ['ppocr', 'paddle', 'paddlex', 'paddleocr']:
    _logging.getLogger(name).setLevel(_logging.ERROR)

from .main_window import MainWindow

log = logging.getLogger(__name__)


def run_gui(*, start_scan: bool = False, skip_first: int = 0,
            resume_species: str = "", resume_cp: int = 0, scan_queue: str | None = None):
    """Launch the Pokemon Manager GUI."""
    if scan_queue is not None:
        from ..indexer.scan_queue import load_scan_queue
        if start_scan or skip_first or resume_species or resume_cp:
            raise ValueError("A scan queue cannot be combined with automatic default or resume scanning")
        load_scan_queue(scan_queue)
    app = QApplication(sys.argv)
    app.setApplicationName("Pokemon Go Storage Manager")
    app.setStyle("Fusion")

    # Dark-ish Pokemon theme
    from PySide6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(30, 30, 40))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(40, 40, 55))
    palette.setColor(QPalette.AlternateBase, QColor(50, 50, 65))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(55, 55, 70))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.Highlight, QColor(80, 140, 200))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    # Set console logging levels — keep it clean
    logging.getLogger("pokemgr.adb.controller").setLevel(logging.WARNING)
    logging.getLogger("pokemgr.reader.ocr").setLevel(logging.WARNING)
    logging.getLogger("pokemgr.reader.bars").setLevel(logging.WARNING)
    logging.getLogger("pokemgr.reader.icons").setLevel(logging.WARNING)
    logging.getLogger("pokemgr.reader.gender").setLevel(logging.WARNING)
    logging.getLogger("pokemgr.reader.ocr_engine").setLevel(logging.WARNING)

    window = MainWindow()
    window.show()
    if start_scan:
        from functools import partial
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, partial(
            window.start_default_scan, skip_first=skip_first,
            resume_species=resume_species, resume_cp=resume_cp,
        ))
    elif scan_queue is not None:
        from functools import partial
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, partial(window.start_scan_queue, scan_queue))
    sys.exit(app.exec())
