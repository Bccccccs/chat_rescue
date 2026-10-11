"""Real encrypted account recognition after a synthetic capture provider."""
import argparse,json,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
import test_cli
from test_workflow import B
from chat_rescue import __main__ as cli
from chat_rescue.operations import ALL
from chat_rescue.workflow import private_json,digest

class CaptureScopeTests(unittest.TestCase):
    setUp=test_cli.CLITests.setUp
    tearDown=test_cli.CLITests.tearDown
    encrypted=test_cli.CLITests.encrypted

    def capture(self,required_count):
        original=self.root/'original.app';copy=self.root/'copy.app'
        for app in (original,copy):
            exe=app/'Contents/MacOS/WeChat';exe.parent.mkdir(parents=True);exe.write_bytes(b'synthetic executable')
        private_json(copy.with_suffix('.prepare.json'),{'original':str(original),'signature':'synthetic signature','copy_executable_sha256':digest(copy/'Contents/MacOS/WeChat'),'original_executable_sha256':digest(original/'Contents/MacOS/WeChat')})
        output=self.root/'captured.json'
        args=argparse.Namespace(app=str(copy),original_app=str(original),db_root=str(self.root),db_roots=[str(self.target[0]),str(self.source[0])],output=str(output),timeout=30)
        def app_info(path):
            app=Path(path);return app,app/'Contents/MacOS/WeChat'
        def system(args):return '/synthetic/lldb' if args[0]=='/usr/bin/lldb' else 'synthetic signature'
        def provider(args,**kwargs):
            if args[0]=='/usr/bin/python3':
                original_keys=json.loads(self.source[1].read_text())['keys']
                private_json(output,{'keys':{str((self.source[0]/rel).relative_to(self.root)):original_keys[rel] for rel in ALL[:required_count]}})
                groups=json.loads(Path(args[args.index('--completion-groups')+1]).read_text())
                self.assertEqual(len(groups),2);self.assertTrue(all(len(group)==4 for group in groups))
            return subprocess.CompletedProcess(args,0)
        with patch.object(cli,'app_info',side_effect=app_info),patch.object(cli,'run',side_effect=system),patch.object(cli,'ensure_closed'),patch.object(cli,'mac_only'),patch.object(cli.subprocess,'run',side_effect=provider):
            return cli.capture(args)

    def test_identifies_nickname_without_requiring_user_to_pick_directory(self):
        result=self.capture(4)
        self.assertEqual(result['accounts'],[{'root':str(self.source[0].resolve()),'owner':B,'nickname':'Partner Demo'}])
        self.assertEqual(result['verified_required_databases'],4)

    def test_partial_account_capture_cannot_advance_wizard(self):
        with self.assertRaises(RuntimeError):self.capture(3)
