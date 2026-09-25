import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core import Transcript
from recovery import RecoveryStore, RECOVERY_SECONDS, SUCCESS_AUDIO_SECONDS


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'recovery'
        self.store = RecoveryStore(self.root)

    def record(self, status='paused'):
        journal = self.store.begin({'language':'en', 'api_key':'must-not-be-saved'})
        journal.write(b'\x12\x34' * 8000)
        journal.close()
        self.store.update(journal.session_id, status)
        return journal.session_id

    def test_abrupt_process_exit_recovers_audio(self):
        # No close/finalize callback runs: this emulates a crash during capture.
        program = """
import os, sys
from recovery import RecoveryStore
s=RecoveryStore(sys.argv[1]);j=s.begin({})
j.write(b'\\x12\\x34'*16000)
os._exit(23)
"""
        child = subprocess.run([sys.executable, '-c', program, str(self.root)],
                               env={**os.environ, 'PYTHONPATH': str(Path(__file__).parent)}, capture_output=True)
        self.assertEqual(child.returncode, 23, child.stderr)
        reopened = RecoveryStore(self.root)
        self.assertEqual(reopened.recover_interrupted(), 1)
        row = reopened.entries()[0]
        self.assertEqual(row['status'], 'interrupted')
        self.assertEqual(reopened.audio(row['id']), b'\x12\x34' * 16000)

    def test_new_recording_and_retry_do_not_replace_prior_results(self):
        first = self.record()
        self.store.complete(first, Transcript('First result.', 'um first result', False))
        second = self.record()
        self.store.complete(first, Transcript('Retry result.', 'retry result', False))
        reopened = RecoveryStore(self.root)
        self.assertEqual(len(reopened.entries()), 2)
        self.assertEqual(reopened.audio(first), reopened.audio(second))
        self.assertEqual([v['text'] for v in reopened.versions(first)], ['Retry result.', 'First result.'])
        self.assertEqual(reopened.get(first)['text'], 'Retry result.')
        self.assertNotIn('must-not-be-saved', reopened.get(first)['settings'])

    def test_separate_audio_and_text_retention(self):
        now = 1800000000
        with patch('recovery.time.time', return_value=now):
            success = self.record()
            failure = self.record('failed')
            self.store.complete(success, Transcript('Keep my text.', 'Keep my text.', False))
        self.store.cleanup(now + SUCCESS_AUDIO_SECONDS - 1)
        self.assertTrue(self.store.path(success).exists())
        self.store.cleanup(now + SUCCESS_AUDIO_SECONDS + 1)
        self.assertFalse(self.store.path(success).exists())
        self.assertTrue(self.store.path(failure).exists())
        self.assertEqual(self.store.get(success)['text'], 'Keep my text.')
        self.store.cleanup(now + RECOVERY_SECONDS - 1)
        self.assertEqual(len(self.store.entries()), 2)
        self.store.cleanup(now + RECOVERY_SECONDS + 1)
        self.assertEqual(self.store.entries(), [])
        self.assertFalse(self.store.path(failure).exists())

    def test_cleanup_never_removes_in_progress_or_protected_audio(self):
        with patch('recovery.time.time', return_value=1000):
            recording = self.record('recording')
            failed = self.record('failed')
        self.store.cleanup(1000 + RECOVERY_SECONDS + 1, protected={failed})
        self.assertTrue(self.store.path(recording).exists())
        self.assertTrue(self.store.path(failed).exists())

    def test_trash_is_reversible_and_permanent_delete_requires_trash(self):
        sid = self.record()
        self.store.complete(sid, Transcript('Recoverable.', 'Recoverable.', False))
        with self.assertRaises(ValueError):
            self.store.delete_permanently(sid)
        self.store.trash(sid)
        self.assertEqual(len(self.store.entries()), 0)
        self.assertEqual(len(self.store.entries(True)), 1)
        self.assertTrue(self.store.path(sid).exists())
        self.store.trash(sid, False)
        self.assertEqual(self.store.get(sid)['text'], 'Recoverable.')
        self.store.trash(sid)
        self.store.delete_permanently(sid)
        self.assertEqual(self.store.entries(True), [])

    def test_private_files_and_wav_export(self):
        sid = self.record()
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path(sid).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.db.stat().st_mode & 0o777, 0o600)
        target = Path(self.temp.name) / 'out.wav'
        self.store.export_wav(sid, target)
        import wave
        with wave.open(str(target)) as wav:
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.readframes(8000), self.store.audio(sid))


if __name__ == '__main__':
    unittest.main()
