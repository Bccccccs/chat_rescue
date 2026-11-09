"""Current-user SID checks only: no WeChat, account libraries or keys."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from chat_rescue import runtime


@unittest.skipUnless(os.name == 'nt', 'Windows native user token')
class WindowsSidTests(unittest.TestCase):
    def setUp(self):
        runtime.current_sid.cache_clear()
        self.addCleanup(runtime.current_sid.cache_clear)
        self.adv = C.WinDLL('advapi32', use_last_error=True)
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        libraries = {'advapi32': self.adv, 'kernel32': self.kernel}
        binding = patch.object(runtime.C, 'WinDLL', side_effect=lambda name, **kw: libraries[name])
        binding.start()
        self.addCleanup(binding.stop)
        # Bind real pointer signatures before wrapping individual native calls.
        self.expected = runtime.current_sid()
        runtime.current_sid.cache_clear()
        self.kernel.CloseHandle = Mock(wraps=self.kernel.CloseHandle)
        self.kernel.LocalFree = Mock(wraps=self.kernel.LocalFree)

    def test_identity_ignores_shell_output_environment_and_releases_resources(self):
        with patch.object(runtime.subprocess, 'run', side_effect=AssertionError('no identity subprocess')), \
             patch.dict(os.environ, USERNAME='synthetic-user', USERDOMAIN='synthetic-domain'):
            self.assertEqual(runtime.current_sid(), self.expected)
            self.assertEqual(runtime.current_sid(), self.expected)
        self.kernel.CloseHandle.assert_called_once()
        self.kernel.LocalFree.assert_called_once()

    def test_native_failure_names_stage_and_error_without_guessing_identity(self):
        original = self.adv.GetTokenInformation
        for stage in ('open', 'size', 'read', 'convert'):
            with self.subTest(stage=stage):
                runtime.current_sid.cache_clear()
                self.kernel.CloseHandle.reset_mock()
                self.kernel.LocalFree.reset_mock()

                def deny(*args):
                    C.set_last_error(5)
                    return 0

                def query(token, kind, data, size, length):
                    if (stage == 'size' and data is None) or (stage == 'read' and data is not None):
                        return deny()
                    return original(token, kind, data, size, length)

                with patch.object(self.adv, 'OpenProcessToken',
                                  Mock(side_effect=deny) if stage == 'open' else self.adv.OpenProcessToken), \
                     patch.object(self.adv, 'GetTokenInformation', Mock(side_effect=query)), \
                     patch.object(self.adv, 'ConvertSidToStringSidW',
                                  Mock(side_effect=deny) if stage == 'convert' else self.adv.ConvertSidToStringSidW), \
                     patch.object(runtime.subprocess, 'run', side_effect=AssertionError('no fallback')):
                    with self.assertRaisesRegex(RuntimeError, 'Windows 错误码 5') as failure:
                        runtime.current_sid()
                expected_stage = {'open': 'OpenProcessToken', 'size': 'GetTokenInformation/size',
                                  'read': 'GetTokenInformation/read', 'convert': 'ConvertSidToStringSidW'}[stage]
                self.assertIn(expected_stage, str(failure.exception))
                self.assertEqual(self.kernel.CloseHandle.call_count, int(stage != 'open'))
                self.kernel.LocalFree.assert_not_called()
                self.assertEqual(runtime.current_sid.cache_info().currsize, 0)

    def test_invalid_token_lengths_stop_before_reading_memory(self):
        for size in (0, 1, 65537):
            with self.subTest(size=size):
                runtime.current_sid.cache_clear()
                self.kernel.CloseHandle.reset_mock()

                def invalid_size(token, kind, data, capacity, length):
                    C.cast(length, C.POINTER(W.DWORD))[0] = size
                    C.set_last_error(122)
                    return 0

                with patch.object(self.adv, 'GetTokenInformation', Mock(side_effect=invalid_size)), \
                     patch.object(self.adv, 'ConvertSidToStringSidW', Mock()) as convert:
                    with self.assertRaisesRegex(RuntimeError, 'TokenUser 长度无效'):
                        runtime.current_sid()
                convert.assert_not_called()
                self.kernel.CloseHandle.assert_called_once()

    def test_matches_independent_dotnet_identity(self):
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value'],
                                check=True, capture_output=True, encoding='ascii',
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertTrue(runtime.current_sid() == result.stdout.strip(), 'native/.NET identity mismatch')

    def test_private_directory_and_other_user_rejection(self):
        with tempfile.TemporaryDirectory(prefix='chat-rescue-sid-中文-') as temporary:
            directory = Path(temporary) / 'private'
            directory.mkdir()
            runtime.make_private(directory)
            path = directory / 'synthetic.txt'
            path.write_text('synthetic public fixture', encoding='utf-8')
            runtime.require_private(path)
            subprocess.run(['icacls.exe', str(path), '/grant', '*S-1-1-0:R'],
                           check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            with self.assertRaisesRegex(RuntimeError, '其他用户访问'):
                runtime.require_private(path)

    def test_no_console_utf8_backend_creates_private_chinese_workspace(self):
        with tempfile.TemporaryDirectory(prefix='chat-rescue-sid-backend-') as temporary:
            directory = Path(temporary) / '中文工作目录'
            code = ('import json; from chat_rescue.windows_backend import workspace, saved_settings; '
                    'from chat_rescue.runtime import require_private; '
                    'path=workspace(); require_private(path); '
                    'print(json.dumps({"private":True,"settings":saved_settings()}))')
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code],
                                    env=dict(os.environ, CHAT_RESCUE_WORKSPACE=str(directory)),
                                    check=True, capture_output=True, encoding='utf-8',
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(json.loads(result.stdout), {'private': True, 'settings': {}})
