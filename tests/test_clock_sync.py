"""Clock bounds and the persistent helper protocol, with no device access."""
import unittest
import io
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pokemgr.adb import clock_sync as clocks
from pokemgr.adb.clock_sync import AndroidClockSync, ClockSample, ClockSampler, ClockSyncError
BOOT = '12345678-1234-1234-1234-123456789abc'
NONCE = 'ab' * 16

def sample(**changes):
    fields = dict(device_serial='phone', boot_id=BOOT, host_send_ns=1000000, host_receive_ns=1002000, device_before_ns=500100, device_after_ns=500200, device_boottime_ns=700150)
    return ClockSample(**fields | changes)

def next_sample(**changes):
    fields = dict(host_send_ns=2000000, host_receive_ns=2001000, device_before_ns=1500100, device_after_ns=1500200, device_boottime_ns=1700150)
    return sample(**fields | changes)

def reply(nonce=NONCE):
    return f'POKEMGR_CLOCK_V1 {nonce} {BOOT} 500100 700150 500200'

def fake_process():
    return SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), poll=Mock(return_value=None), wait=Mock(return_value=0), terminate=Mock(), kill=Mock())

def prepared_sampler(monkeypatch):
    adb = SimpleNamespace(serial='phone', adb_path='/fake/adb')
    sampler = ClockSampler(adb)
    process = fake_process()
    starts = []

    def start():
        starts.append(True)
        sampler._process = process
    monkeypatch.setattr(sampler, '_start', start)
    monkeypatch.setattr(clocks, 'monotonic_ns', lambda: 1000000)
    monkeypatch.setattr(clocks.secrets, 'token_hex', lambda size: NONCE)
    monkeypatch.setattr(sampler, '_write_request', Mock())
    monkeypatch.setattr(sampler, '_read_line', lambda deadline: (reply(), 1002000))
    return (sampler, process, starts)

class ClockSyncTests(unittest.TestCase):

    def setUp(self):
        self.monkeypatch = SimpleNamespace(setattr=lambda target, name, value: self.enterContext(patch.object(target, name, value)))

    def test_reply_uses_bracket_and_preserves_all_fields(self):
        parsed = ClockSample.from_reply(reply() + '\n', nonce=NONCE, device_serial='phone', host_send_ns=1000000, host_receive_ns=1002000)
        self.assertEqual(parsed, sample())
        self.assertEqual(parsed.round_trip_ns, 2000)

    def test_reply_rejects_malformed_or_wrong_request(self):
        for bad_reply in [reply('cd' * 16), reply() + '\n' + reply(), reply().replace('500100', '-1'), reply().replace('500100', '0'), reply().replace('500100', str(1 << 63)), reply().replace(BOOT, 'bad-boot'), b'not text']:
            with self.subTest(bad_reply=bad_reply):
                with self.assertRaises(ClockSyncError):
                    ClockSample.from_reply(bad_reply, nonce=NONCE, device_serial='phone', host_send_ns=1000000, host_receive_ns=1002000)

    def test_sample_rejects_invalid_bounds(self):
        for changes in [{'host_send_ns': True}, {'host_receive_ns': 999999}, {'device_after_ns': 500099}, {'device_boottime_ns': 500099}, {'device_serial': ' '}, {'boot_id': None}]:
            with self.subTest(changes=changes):
                with self.assertRaises(ClockSyncError):
                    sample(**changes)

    def test_mapping_keeps_entire_round_trip_and_microsecond_rounding(self):
        sync = AndroidClockSync(max_drift_ppm=0)
        self.assertEqual(sync.observe(sample()), 1)
        bounds = sync.map_pts_us(501, now_ns=1010000, received_ns=1010000)
        self.assertEqual(bounds.earliest_ns, 1000800)
        self.assertEqual(bounds.latest_ns, 1003899)
        self.assertEqual(bounds.uncertainty_ns, 3099)
        self.assertFalse(bounds.strictly_after(1000800))
        self.assertTrue(bounds.strictly_after(1000799))
        self.assertFalse(bounds.strictly_after(True))
        self.assertFalse(bounds.fresh_at(1010000, max_age_ns=9199))
        self.assertTrue(bounds.fresh_at(1010000, max_age_ns=9200))

    def test_decoder_receipt_only_tightens_upper_bound(self):
        sync = AndroidClockSync(max_drift_ppm=0)
        sync.observe(sample())
        bounds = sync.map_pts_us(501, now_ns=1010000, received_ns=1001000)
        self.assertEqual((bounds.earliest_ns, bounds.latest_ns), (1000800, 1001000))
        self.assertFalse(bounds.strictly_after(1001000))

    def test_drift_widens_bounds_with_distance_from_calibration(self):
        exact, drifting = (AndroidClockSync(max_drift_ppm=0), AndroidClockSync())
        for sync in (exact, drifting):
            sync.observe(sample())
        baseline = exact.map_pts_us(1500, now_ns=2010000)
        expanded = drifting.map_pts_us(1500, now_ns=2010000)
        self.assertLess(expanded.earliest_ns, baseline.earliest_ns)
        self.assertGreater(expanded.latest_ns, baseline.latest_ns)

    def test_compatible_observations_intersect_and_change_generation(self):
        sync = AndroidClockSync(max_drift_ppm=0)
        sync.observe(sample())
        old = sync.map_pts_us(501, now_ns=1010000)
        self.assertEqual(sync.observe(next_sample()), 2)
        bounds = sync.map_pts_us(1501, now_ns=2010000)
        self.assertEqual(bounds.uncertainty_ns, 2099)
        self.assertNotEqual(bounds.sync_generation, old.sync_generation)

    def test_clock_changes_invalidate_all_old_evidence(self):
        for changed in [{'device_serial': 'other'}, {'boot_id': '22345678-1234-1234-1234-123456789abc'}, {'host_send_ns': 1001999}, {'device_before_ns': 500200, 'device_after_ns': 500300}, {'device_boottime_ns': 1701000}, {'host_send_ns': 2100000, 'host_receive_ns': 2101000}]:
            with self.subTest(changed=changed):
                sync = AndroidClockSync(max_drift_ppm=0)
                sync.observe(sample())
                with self.assertRaises(ClockSyncError):
                    sync.observe(next_sample(**changed))
                self.assertEqual(sync.generation, 2)
                with self.assertRaisesRegex(ClockSyncError, 'not been synchronized'):
                    sync.map_pts_us(1501, now_ns=2200000)

    def test_missing_slow_or_impossible_sample_fails_closed(self):
        for bad in [None, sample(host_receive_ns=2100000), sample(device_after_ns=503000)]:
            with self.subTest(bad=bad):
                sync = AndroidClockSync(max_rtt_ns=1000000, max_drift_ppm=0)
                with self.assertRaises(ClockSyncError):
                    sync.observe(bad)
                self.assertEqual(sync.generation, 1)

    def test_expiry_and_explicit_invalidation_require_new_calibration(self):
        sync = AndroidClockSync(max_age_ns=10000, max_drift_ppm=0)
        sync.observe(sample())
        bounds = sync.map_pts_us(501, now_ns=1010000)
        self.assertFalse(bounds.fresh_at(1012001, max_age_ns=100000))
        with self.assertRaisesRegex(ClockSyncError, 'expired'):
            sync.map_pts_us(501, now_ns=1012001)
        self.assertEqual(sync.generation, 2)
        sync.observe(next_sample())
        sync.invalidate()
        self.assertEqual(sync.generation, 4)
        with self.assertRaisesRegex(ClockSyncError, 'not been synchronized'):
            sync.map_pts_us(1501, now_ns=2010000)

    def test_invalid_or_future_source_times_invalidate(self):
        for pts, now, received in [(0, 1010000, 1010000), (True, 1010000, 1010000), (1 << 61, 1010000, 1010000), (501, 1010000, 1010001), (501, 1001999, 1001999), (10000, 1010000, 1010000)]:
            with self.subTest(pts=pts, now=now, received=received):
                sync = AndroidClockSync(max_drift_ppm=0)
                sync.observe(sample())
                with self.assertRaises(ClockSyncError):
                    sync.map_pts_us(pts, now_ns=now, received_ns=received)
                self.assertEqual(sync.generation, 2)

    def test_persistent_sampler_starts_once_and_reuses_owned_process(self):
        monkeypatch = self.monkeypatch
        sampler, process, starts = prepared_sampler(monkeypatch)
        with sampler:
            self.assertEqual([sampler.sample() for _ in range(3)], [sample()] * 3)
        self.assertEqual(len(starts), 1)
        self.assertEqual(sampler._write_request.call_count, 3)
        self.assertTrue(process.stdin.closed and process.stdout.closed)
        process.wait.assert_called_once_with(timeout=1)
        sampler.close()
        with self.assertRaisesRegex(ClockSyncError, 'closed'):
            sampler.sample()

    def test_ready_handshake_is_outside_sample_round_trip(self):
        monkeypatch = self.monkeypatch
        sampler, process, _starts = prepared_sampler(monkeypatch)
        monkeypatch.setattr(sampler, '_start', ClockSampler._start.__get__(sampler))
        process.stdin.fileno = lambda: 20
        process.stdout.fileno = lambda: 21
        spawn = Mock(return_value=process)
        monkeypatch.setattr(clocks.subprocess, 'Popen', spawn)
        monkeypatch.setattr(clocks.os, 'set_blocking', Mock())
        times = iter([100, 1000000])
        monkeypatch.setattr(clocks, 'monotonic_ns', lambda: next(times))
        reads = iter([('POKEMGR_CLOCK_READY_V1', 900000), (reply(), 1002000)])
        monkeypatch.setattr(sampler, '_read_line', lambda deadline: next(reads))
        self.assertEqual(sampler.sample().round_trip_ns, 2000)
        command = spawn.call_args.args[0]
        self.assertEqual(command[:5], ['/fake/adb', '-s', 'phone', 'shell', '-T'])
        self.assertTrue(command[-1].endswith('pokemgr.tools.ClockSample --loop'))
        sampler.close()

    def test_protocol_failures_close_owned_process_and_preserve_exception(self):
        monkeypatch = self.monkeypatch
        for failure in [ClockSyncError('timed out'), ClockSyncError('exited before replying'), BrokenPipeError('pipe closed')]:
            with self.subTest(failure=failure):
                sampler, process, _starts = prepared_sampler(monkeypatch)
                monkeypatch.setattr(sampler, '_read_line', Mock(side_effect=failure))
                process.wait.side_effect = OSError('cleanup failed')
                with self.assertRaisesRegex(type(failure), str(failure)):
                    sampler.sample()
                self.assertTrue(sampler._closed and sampler._process is None)
                self.assertTrue(process.stdout.closed)

    def test_wrong_nonce_and_device_change_are_rejected(self):
        monkeypatch = self.monkeypatch
        sampler, _process, _starts = prepared_sampler(monkeypatch)
        monkeypatch.setattr(sampler, '_read_line', lambda deadline: (reply('cd' * 16), 1002000))
        with self.assertRaisesRegex(ClockSyncError, 'another request'):
            sampler.sample()
        sampler, process, _starts = prepared_sampler(monkeypatch)

        def changed(deadline):
            sampler.adb.serial = 'other'
            return (reply(), 1002000)
        monkeypatch.setattr(sampler, '_read_line', changed)
        with self.assertRaisesRegex(ClockSyncError, 'Selected device changed'):
            sampler.sample()
        self.assertTrue(process.stdout.closed)

    def test_pipe_reader_rejects_invalid_protocol(self):
        monkeypatch = self.monkeypatch
        for payload, error in [(b'x' * 512, 'protocol limit'), (b'ready\nextra\n', 'unsolicited extra'), (b'\xff\n', 'not ASCII'), (b'', 'exited before replying')]:
            with self.subTest(payload=payload, error=error):
                sampler, process, _starts = prepared_sampler(monkeypatch)
                sampler._process = process
                process.stdout.fileno = lambda: 10
                monkeypatch.setattr(sampler, '_read_line', ClockSampler._read_line.__get__(sampler))
                monkeypatch.setattr(sampler, '_wait_pipe', Mock())
                monkeypatch.setattr(clocks.os, 'read', lambda fd, size: payload)
                with self.assertRaisesRegex(ClockSyncError, error):
                    sampler._read_line(2000000)

    def test_nonblocking_reader_handles_fragmented_reply(self):
        monkeypatch = self.monkeypatch
        sampler, process, _starts = prepared_sampler(monkeypatch)
        sampler._process = process
        process.stdout.fileno = lambda: 10
        monkeypatch.setattr(sampler, '_read_line', ClockSampler._read_line.__get__(sampler))
        monkeypatch.setattr(sampler, '_wait_pipe', Mock())
        reads = Mock(side_effect=[BlockingIOError(), b'POKEMGR_', b'CLOCK_READY_V1\n'])
        monkeypatch.setattr(clocks.os, 'read', reads)
        self.assertEqual(sampler._read_line(2000000), ('POKEMGR_CLOCK_READY_V1', 1000000))
        self.assertEqual(reads.call_count, 3)

    def test_reply_after_deadline_is_rejected_even_if_pipe_was_ready(self):
        monkeypatch = self.monkeypatch
        sampler, process, _starts = prepared_sampler(monkeypatch)
        sampler._process = process
        process.stdout.fileno = lambda: 10
        monkeypatch.setattr(sampler, '_read_line', ClockSampler._read_line.__get__(sampler))
        monkeypatch.setattr(sampler, '_wait_pipe', Mock())
        monkeypatch.setattr(clocks.os, 'read', lambda fd, size: b'ready\n')
        with self.assertRaisesRegex(ClockSyncError, 'timed out'):
            sampler._read_line(999999)

    def test_wait_pipe_has_deadline_without_device(self):
        monkeypatch = self.monkeypatch
        monkeypatch.setattr(clocks, 'monotonic_ns', lambda: 100)
        with self.assertRaisesRegex(ClockSyncError, 'timed out'):
            ClockSampler._wait_pipe(None, 1, 100)
        selector = Mock()
        selector.__enter__ = Mock(return_value=selector)
        selector.__exit__ = Mock(return_value=False)
        selector.select.return_value = []
        monkeypatch.setattr(clocks.selectors, 'DefaultSelector', lambda: selector)
        with self.assertRaisesRegex(ClockSyncError, 'timed out'):
            ClockSampler._wait_pipe(None, 1, 1000100)
        selector.select.assert_called_once_with(0.001)

    def test_close_escalates_only_owned_process_and_is_idempotent(self):
        monkeypatch = self.monkeypatch
        sampler, process, _starts = prepared_sampler(monkeypatch)
        sampler._process = process
        process.wait.side_effect = [subprocess.TimeoutExpired('helper', 1)] * 2 + [0]
        sampler.close()
        sampler.close()
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 3)
if __name__ == '__main__':
    unittest.main()
