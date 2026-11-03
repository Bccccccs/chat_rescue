"""Windows host checks against synthetic files only; never launches WeChat."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from chat_rescue import runtime
from chat_rescue import __main__ as cli
from chat_rescue.vendor.sqlcipher_probe import load_key
from chat_rescue.workflow import private_json


@unittest.skipUnless(os.name == 'nt', 'Windows native services')
class WindowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='chat-rescue-中文-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_lock_blocks_another_process_and_releases_after_exception(self):
        path = self.root / '.lock'
        code = ('from chat_rescue.runtime import exclusive_lock; import sys\n'
                'with exclusive_lock(sys.argv[1]): pass\n')
        with self.assertRaisesRegex(ValueError, 'synthetic'):
            with runtime.exclusive_lock(path):
                p = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True)
                self.assertNotEqual(p.returncode, 0)
                raise ValueError('synthetic')
        p = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_running_wechat_and_failed_process_queries_block(self):
        for name in ('WeChat.exe', 'Weixin.exe', 'WEIXIN.EXE'):
            result = subprocess.CompletedProcess([], 0, f'"{name}","123","Console","1","1 K"\n')
            with patch.object(runtime.subprocess, 'run', return_value=result):
                with self.assertRaises(RuntimeError): runtime.ensure_closed()
        with patch.object(runtime.subprocess, 'run', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError): runtime.ensure_closed()
        with patch.object(runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'invalid')):
            with self.assertRaises(RuntimeError): runtime.ensure_closed()

    def test_key_paths_absolute_backslash_relative_and_bom(self):
        database = self.root / 'message/message_0.db'
        database.parent.mkdir(); database.write_bytes(bytes(range(16)))
        for index, name in enumerate((str(database), 'message\\message_0.db', database.read_bytes().hex())):
            key = self.root / f'keys-{index}.json'
            private_json(key, {'keys': {name: {'enc_key': 'ab' * 32}}})
            key.write_text('\ufeff' + key.read_text(encoding='utf-8'), encoding='utf-8')
            self.assertEqual(load_key(key, database), 'ab' * 32)

    def test_acl_rejects_other_users_even_after_chmod(self):
        path = self.root / 'keys.json'
        private_json(path, {})
        runtime.require_private(path)
        subprocess.run(['icacls.exe', str(path), '/grant', '*S-1-1-0:R'],
                       check=True, capture_output=True)
        path.chmod(0o600)
        with self.assertRaises(RuntimeError): runtime.require_private(path)

    def test_custom_data_root_discovery_keeps_all_accounts(self):
        for owner in ('synthetic_a', 'synthetic_b'):
            (self.root / owner / 'db_storage').mkdir(parents=True)
        found = cli.roots([self.root])['database_roots']
        self.assertTrue(all(str((self.root / owner / 'db_storage').resolve()) in found
                            for owner in ('synthetic_a', 'synthetic_b')))

    def test_doctor_does_not_require_lldb_or_claim_capture(self):
        with patch.object(cli, 'library'), patch.object(cli, 'run', side_effect=AssertionError('must not run LLDB')):
            result = cli.doctor()
        self.assertTrue(result['ready'])
        self.assertFalse(result['key_capture_available'])

    def test_mac_capture_rejected_before_external_commands(self):
        with patch.object(cli, 'run', side_effect=AssertionError('must not sign')):
            with self.assertRaises(RuntimeError): cli.prepare('synthetic.app', 'copy.app')
