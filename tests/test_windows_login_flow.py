"""Exercise the actual C# import gate without opening WPF or WeChat."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from chat_rescue import windows_backend as backend


class SourceImportBackendTests(unittest.TestCase):
    def test_b_read_is_blocked_until_desktop_history_is_confirmed(self):
        selected = {'root': 'synthetic', 'owner': 'synthetic_owner', 'keys': 'synthetic.json'}
        with patch.object(backend, 'account', return_value=selected), \
                patch.object(backend, 'main_processes', return_value=[]), \
                patch.object(backend, 'ensure_closed'), patch.object(backend, 'require_private'), \
                patch.object(backend.cli, 'verify_keys'), patch.object(backend, 'account_nickname', return_value='Synthetic'):
            with self.assertRaisesRegex(ValueError, '导入|记录'):
                backend.dispatch({'action': 'capture', 'slot': 'B', 'account': {},
                                  'identity_confirmed': True, 'executable': 'synthetic.exe'})

    def test_plan_is_blocked_without_b_import_confirmation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backend, 'ensure_closed'), \
                patch.object(backend, 'account', return_value={'keys': 'synthetic.json'}), \
                patch.object(backend, 'require_private'), patch.object(backend, 'job', return_value=Path(directory)), \
                patch.object(backend, 'make_sync_plan'), patch.object(backend, 'plan_view', return_value={}):
            with self.assertRaisesRegex(ValueError, '导入|记录'):
                backend.dispatch({'action': 'plan', 'a': {}, 'b': {}, 'identity_confirmed': True})


@unittest.skipUnless(os.name == 'nt', 'Windows .NET Framework login state')
class SourceImportFlowTests(unittest.TestCase):
    def test_import_reminder_timing_and_read_gate(self):
        compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():
            self.skipTest('.NET Framework compiler unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe = root/'Probe.cs'
            probe.write_text('''using System;
using ChatRescueWindows;
class Probe {
 static int Main() {
  var flow = new SourceImportFlow();
  if(flow.Reminder("A", false) != "" || flow.Reminder("B", true) != "") return 1;
  if(!flow.Reminder("B", false).Contains("现在从手机 B")) return 2;
  if(!flow.Reminder("B", false).Contains("导入电脑微信 B")) return 3;
  if(flow.CanRead("B", true) || !flow.CanRead("A", false)) return 4;
  flow.Confirm(true);
  if(flow.CanRead("B", false) || !flow.CanRead("B", true)) return 5;
  flow.Reset();
  if(flow.CanRead("B", true)) return 6;
  flow.Confirm(true); flow.Confirm(false);
  if(flow.CanRead("B", true) || flow.CanRead("unknown", true)) return 7;
  return 0;
 }
}''', encoding='utf-8-sig')
            executable = root/'Probe.exe'
            production = Path(__file__).resolve().parents[1]/'desktop/windows/SourceImportFlow.cs'
            result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/out:'+str(executable),
                                     str(probe), str(production)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout.decode('utf-8', 'replace'))
            result = subprocess.run([str(executable)], capture_output=True)
            self.assertEqual(result.returncode, 0, 'source import gate failed stage ' + str(result.returncode))
