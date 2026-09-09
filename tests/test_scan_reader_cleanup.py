"""OCR lifetime includes setup failures before a scan session is created."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pokemgr.indexer.state_machine import IndexingStateMachine


class ScanReaderCleanupTests(unittest.TestCase):
    def test_reader_closes_when_device_or_database_setup_fails(self):
        for failure in ("device", "database"):
            with self.subTest(failure=failure):
                scanner = object.__new__(IndexingStateMachine)
                scanner.reader = Mock()
                scanner._reader_threads = []
                scanner.adb = Mock()
                scanner.db = Mock()
                scanner.session_id = "setup-test"
                scanner.use_calculated_cp = True
                scanner.use_cp_animation = True
                scanner.adb.get_device_info.return_value = SimpleNamespace(fingerprint="phone")
                if failure == "device":
                    scanner.adb.get_device_info.side_effect = RuntimeError("device gone")
                else:
                    scanner.db.create_session.side_effect = RuntimeError("database closed")
                with patch("pokemgr.reader.ocr_engine._get_paddle", return_value=None):
                    with self.assertRaises(RuntimeError):
                        scanner.start()
                scanner.reader.prepare_native_ocr.assert_called_once()
                scanner.reader.close.assert_called_once()
                scanner.db.complete_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
