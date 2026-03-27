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


def run_gui():
    """Launch the Pokemon Manager GUI."""
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
    sys.exit(app.exec())
