import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tes3x.net.server import save_world


class StateSaveTests(unittest.TestCase):
    def locked(self, code=5):
        error = PermissionError('file held by a reader')
        error.winerror = code
        return error

    def test_brief_windows_reader_lock_retries_the_same_staged_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'stream.json'
            path.write_text('{"old": true}', encoding='utf-8')
            import os
            replace = os.replace
            calls = []

            def briefly_locked(source, target):
                calls.append((source, target))
                self.assertEqual(json.loads(Path(source).read_text()), {'new': True})
                self.assertEqual(json.loads(path.read_text()), {'old': True})
                if len(calls) == 1:
                    raise self.locked()
                replace(source, target)

            with patch('tes3x.net.server.os.replace', side_effect=briefly_locked), \
                    patch('tes3x.net.server.time.sleep'):
                save_world(str(path), {'new': True})
            self.assertEqual(calls, [(str(path) + '.tmp', str(path))] * 2)
            self.assertEqual(json.loads(path.read_text()), {'new': True})
            self.assertFalse(Path(str(path) + '.tmp').exists())

    def test_persistent_lock_is_bounded_and_preserves_both_whole_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'stream.json'
            path.write_text('{"old": true}', encoding='utf-8')
            with patch('tes3x.net.server.os.replace', side_effect=self.locked(32)) as replace, \
                    patch('tes3x.net.server.time.monotonic', side_effect=[0., 0., 0.25]), \
                    patch('tes3x.net.server.time.sleep'):
                with self.assertRaises(PermissionError):
                    save_world(str(path), {'new': True})
            self.assertEqual(replace.call_count, 2)
            self.assertEqual(json.loads(path.read_text()), {'old': True})
            self.assertEqual(json.loads(Path(str(path) + '.tmp').read_text()), {'new': True})

    def test_unrelated_permission_errors_are_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('tes3x.net.server.os.replace', side_effect=PermissionError) as replace:
                with self.assertRaises(PermissionError):
                    save_world(str(Path(folder) / 'stream.json'), {})
            replace.assert_called_once()
