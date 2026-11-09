"""Binding diagnostics use file-use metadata and redact account identity."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chat_rescue import windows_backend

spec = importlib.util.spec_from_file_location('binding_diagnostic',
    Path(__file__).resolve().parents[1] / 'desktop/windows/diagnose_binding.py')
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


class BindingDiagnosticTests(unittest.TestCase):
    def test_db_and_sidecars_keep_separate_evidence_without_reading_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / 'xwechat_files'
            root = base / 'wxid_synthetic_private_c4ab' / 'db_storage'
            for relative in ('message/message_0.db', 'message/message_resource.db',
                             'session/session.db', 'contact/contact.db', 'message/message_0.db-wal'):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'synthetic fixture')
            exe = Path(temporary) / 'Weixin.exe'
            exe.touch()
            with patch.object(windows_backend, 'main_processes', return_value=[123]), \
                 patch.object(windows_backend, 'capture', side_effect=AssertionError('no capture')), \
                 patch.object(diagnostic, 'file_users', side_effect=[[], [123], []]) as query, \
                 patch('builtins.open', side_effect=AssertionError('no DB content reads')):
                result = diagnostic.diagnose(exe, base)
            account = result['accounts'][0]
            self.assertEqual(account['suffix'], 'c4ab')
            self.assertEqual(account['groups']['db']['matched_main_pids'], [])
            self.assertEqual(account['groups']['wal']['matched_main_pids'], [123])
            self.assertEqual(account['groups']['db']['files'], 4)
            self.assertEqual(account['groups']['shm']['files'], 0)
            self.assertEqual(query.call_count, 3)
            self.assertNotIn('wxid_synthetic_private', json.dumps(result))
            self.assertNotIn(temporary, json.dumps(result))

    def test_account_query_error_is_redacted_and_not_a_successful_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / 'xwechat_files'
            (base / 'wxid_synthetic_private_e215/db_storage').mkdir(parents=True)
            exe = Path(temporary) / 'Weixin.exe'
            exe.touch()
            with patch.object(windows_backend, 'main_processes', return_value=[123]), \
                 patch.object(diagnostic, 'file_users', side_effect=AssertionError('invalid inventory')):
                result = diagnostic.diagnose(exe, base)
            account = result['accounts'][0]
            self.assertEqual(account['error'], 'ValueError')
            self.assertNotIn('groups', account)
            self.assertNotIn('wxid_synthetic_private', json.dumps(result))

    def test_rejects_broad_scan_roots_and_non_wechat_executables(self):
        with tempfile.TemporaryDirectory() as temporary:
            exe = Path(temporary) / 'Weixin.exe'
            exe.touch()
            with self.assertRaises(ValueError):
                diagnostic.diagnose(exe, temporary)
            other = Path(temporary) / 'other.exe'
            other.touch()
            base = Path(temporary) / 'xwechat_files'
            base.mkdir()
            with self.assertRaises(ValueError):
                diagnostic.diagnose(other, base)

    @unittest.skipUnless(os.name == 'nt', 'Windows Restart Manager')
    def test_real_file_use_query_observes_only_synthetic_open_file(self):
        self.assertEqual(diagnostic.file_users([]), [])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'synthetic.db'
            with path.open('w+b') as stream:
                stream.write(b'synthetic fixture')
                stream.flush()
                self.assertIn(os.getpid(), diagnostic.file_users([path]))
