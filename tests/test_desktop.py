import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from chat_rescue import desktop_backend as backend
from chat_rescue.__main__ import prepare

class DesktopTests(unittest.TestCase):
    def test_unknown_action_refused(self):
        with self.assertRaises(ValueError): backend.dispatch({'action':'execute-shell','command':'anything'})

    def test_invalid_slot_cannot_escape_application_directory(self):
        with self.assertRaises(ValueError): backend.slot_app('../../elsewhere')

    def test_different_signing_identifiers_are_used_for_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'original.app';exe=original/'Contents/MacOS/WeChat'
            exe.parent.mkdir(parents=True);exe.write_bytes(b'original')
            output=root/'copy.app';calls=[]
            def system(args):
                calls.append(args)
                if args[0]=='/usr/bin/ditto':
                    import shutil;shutil.copytree(args[1],args[2])
                return 'original signature'
            with patch('chat_rescue.__main__.mac_only'), patch('chat_rescue.__main__.ensure_closed'), patch('chat_rescue.__main__.app_info',return_value=(original,exe)), patch('chat_rescue.__main__.run',side_effect=system):
                prepare(original,output,signing_id='local.chat-rescue.wechat.A')
            sign=[c for c in calls if '--sign' in c][-1]
            self.assertIn('--identifier',sign)
            self.assertNotIn('--deep',sign)
            self.assertTrue(any('--deep' in c and '--identifier' not in c for c in calls))
            self.assertIn('local.chat-rescue.wechat.A',sign)
            self.assertEqual(exe.read_bytes(),b'original')
            self.assertEqual(json.loads(output.with_suffix('.prepare.json').read_text())['signing_identifier'],'local.chat-rescue.wechat.A')

class TerminalLaunchTests(unittest.TestCase):
    def test_terminal_request_refuses_arbitrary_or_recursive_actions(self):
        for action in ('execute-shell','authorized-request','launch'):
            with self.assertRaisesRegex(ValueError,'不支持'):
                backend.authorized_request({'action':action})

    def test_terminal_request_propagates_verified_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def opened(args):
                request=json.loads((root/'operation-request.json').read_text())
                self.assertEqual(request['action'],'inspect')
                (root/'operation-result.json').write_text(json.dumps({'ok':True,'result':{'owner':{'nick_name':'合成昵称'}}}))
            with patch.object(backend,'new_job',return_value=root),patch.object(backend.cli,'run',side_effect=opened):
                result=backend.authorized_request({'action':'inspect','account':{'root':'/synthetic'}})
            self.assertEqual(result['owner']['nick_name'],'合成昵称')

    def test_failed_terminal_launch_writes_failure_receipt(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            request=Path(tmp)/'launch-request.json';request.write_text(json.dumps({'slots':['A']}))
            with patch.object(sys,'argv',['backend','--launch-file',str(request)]), patch.object(backend,'launch_slots',side_effect=RuntimeError('账号 A 启动后退出（退出码 255）')):
                self.assertEqual(backend.main(),2)
            result=json.loads((request.parent/'launch-result.json').read_text())
            self.assertFalse(result['ok']);self.assertIn('255',result['error'])

    def test_terminal_failure_is_propagated_to_gui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def opened(args):
                (root/'launch-result.json').write_text(json.dumps({'ok':False,'error':'启动后退出255'}))
            with patch.object(backend,'new_job',return_value=root), patch.object(backend.cli,'app_info'),patch.object(backend.cli,'run',side_effect=opened):
                with self.assertRaisesRegex(RuntimeError,'255'):backend.launch_via_terminal(['A'])

class ResumeTests(unittest.TestCase):
    def test_plan_resume_preserves_partial_result_and_account_identity(self):
        from chat_rescue.workflow import private_json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            private_json(root/'sync-plan.json',{'version':1,'accounts':{'A':{'owner':'demo_a'},'B':{'owner':'demo_b'}},'directions':{'A':{'added':3},'B':{'added':2}}})
            private_json(root/'sync-result.json',{'state':'partial','accounts':{'A':{'state':'applied'}}})
            r=backend.dispatch({'action':'resume','plan':str(root)})
            self.assertEqual(r['state'],'partial')
            self.assertEqual(r['accounts']['A']['owner'],'demo_a')
            self.assertEqual(r['directions']['B']['added'],2)

class SigningIntegrationTests(unittest.TestCase):
    def test_two_synthetic_apps_get_distinct_actual_code_signatures(self):
        import platform,plistlib,shutil,subprocess
        if platform.system()!='Darwin' or platform.machine()!='arm64':self.skipTest('macOS signing integration')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'original.app';exe=original/'Contents/MacOS/WeChat'
            exe.parent.mkdir(parents=True);shutil.copyfile('/usr/bin/true',exe);exe.chmod(0o755)
            (original/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.tencent.xinWeChat','CFBundleShortVersionString':'4.1.13','CFBundleExecutable':'WeChat','CFBundlePackageType':'APPL'}))
            helper=original/'Contents/MacOS/WeChatAppEx.app'
            helper_exe=helper/'Contents/MacOS/Helper';helper_exe.parent.mkdir(parents=True)
            shutil.copyfile('/usr/bin/true',helper_exe);helper_exe.chmod(0o755)
            (helper/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.tencent.flue.WeChatAppEx','CFBundleExecutable':'Helper','CFBundlePackageType':'APPL'}))
            subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(original)],check=True,capture_output=True)
            before=exe.read_bytes();identifiers=[]
            with patch('chat_rescue.__main__.ensure_closed'):
                for slot in ('A','B'):
                    target=root/(slot+'.app');prepare(original,target,signing_id='local.chat-rescue.wechat.'+slot)
                    report=subprocess.run(['/usr/bin/codesign','-dvv',str(target)],check=True,capture_output=True,text=True)
                    identifiers.append(next(line for line in report.stderr.splitlines() if line.startswith('Identifier=')))
                    nested=subprocess.run(['/usr/bin/codesign','-dvv',str(target/'Contents/MacOS/WeChatAppEx.app')],check=True,capture_output=True,text=True)
                    self.assertIn('Identifier=com.tencent.flue.WeChatAppEx',nested.stderr)
            self.assertEqual(identifiers,['Identifier=local.chat-rescue.wechat.A','Identifier=local.chat-rescue.wechat.B'])
            self.assertEqual(exe.read_bytes(),before)

class LaunchTests(unittest.TestCase):
    def test_quick_exit_is_reported_as_failure_instead_of_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable=Path(tmp)/'WeChat';executable.write_text('#!/bin/sh\nexit 7\n');executable.chmod(0o755)
            with patch.object(backend.cli,'app_info',return_value=(Path(tmp),executable)),patch.object(backend,'running_pid',return_value=None):
                with self.assertRaisesRegex(RuntimeError,'7'):
                    backend.dispatch({'action':'launch','slots':['A']})

    def test_launch_detaches_process_and_reports_actual_pid(self):
        import signal,subprocess
        with tempfile.TemporaryDirectory() as tmp:
            executable=Path(tmp)/'WeChat';executable.write_text('#!/bin/sh\nexec /bin/sleep 30\n');executable.chmod(0o755)
            with patch.object(backend.cli,'app_info',return_value=(Path(tmp),executable)),patch.object(backend,'running_pid',return_value=None):
                result=backend.dispatch({'action':'launch','slots':['A']})
            pid=result['launched']['A']['pid']
            try:
                self.assertEqual(os.getsid(pid),pid)
                self.assertEqual(result['launched']['A']['state'],'running')
            finally:
                os.kill(pid,signal.SIGTERM)
                for process in backend.LAUNCHED:
                    if process.pid==pid:process.wait(timeout=3)

    def test_existing_slot_is_not_started_a_second_time(self):
        with patch.object(backend.cli,'app_info',return_value=(Path('/synthetic'),Path('/synthetic/WeChat'))),patch.object(backend,'running_pid',return_value=1234),patch.object(backend.subprocess,'Popen') as popen:
            result=backend.dispatch({'action':'launch','slots':['A']})
            self.assertEqual(result['launched']['A']['state'],'already_running')
            popen.assert_not_called()

class BundleIdentityTests(unittest.TestCase):
    def test_distinct_bundle_identity_retains_verified_copy_marker(self):
        import plistlib,platform,shutil,subprocess
        if platform.system()!='Darwin' or platform.machine()!='arm64':self.skipTest('macOS signing')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'original.app';exe=original/'Contents/MacOS/WeChat'
            exe.parent.mkdir(parents=True);shutil.copyfile('/usr/bin/true',exe);exe.chmod(0o755)
            original_info={'CFBundleIdentifier':'com.tencent.xinWeChat','CFBundleShortVersionString':'4.1.13','CFBundleExecutable':'WeChat','CFBundlePackageType':'APPL'}
            (original/'Contents/Info.plist').write_bytes(plistlib.dumps(original_info))
            subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(original)],check=True,capture_output=True)
            with patch('chat_rescue.__main__.ensure_closed'):
                prepare(original,root/'B.app',signing_id='local.chat-rescue.wechat.B',bundle_identifier='local.chat-rescue.wechat.B')
            info=plistlib.loads((root/'B.app/Contents/Info.plist').read_bytes())
            self.assertEqual(info['CFBundleIdentifier'],'local.chat-rescue.wechat.B')
            self.assertEqual(plistlib.loads((original/'Contents/Info.plist').read_bytes()),original_info)
            self.assertEqual(backend.cli.app_info(root/'B.app')[0],(root/'B.app').resolve())
            (root/'B.prepare.json').unlink()
            with self.assertRaises(ValueError):backend.cli.app_info(root/'B.app')

class RootDiscoveryTests(unittest.TestCase):
    def test_isolated_b_database_directory_is_discoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            original=home/'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/demo_a/db_storage'
            isolated=home/'Library/Containers/local.chat-rescue.wechat.B/Data/Documents/xwechat_files/demo_b/db_storage'
            original.mkdir(parents=True);isolated.mkdir(parents=True)
            with patch.object(backend.cli.Path,'home',return_value=home):
                roots=backend.cli.roots()['database_roots']
            self.assertEqual(set(roots),{str(original),str(isolated)})

class WorkingDirectoryTests(unittest.TestCase):
    def test_launcher_preserves_normal_working_directory(self):
        import shlex,signal
        with tempfile.TemporaryDirectory() as tmp:
            executable=Path(tmp)/'WeChat'
            executable.write_text('#!/bin/sh\n[ "$PWD" = '+shlex.quote(os.getcwd())+' ] || exit 255\nexec /bin/sleep 30\n');executable.chmod(0o755)
            with patch.object(backend.cli,'app_info',return_value=(Path(tmp),executable)),patch.object(backend,'running_pid',return_value=None):
                result=backend.dispatch({'action':'launch','slots':['A']})
            pid=result['launched']['A']['pid']
            try:self.assertEqual(result['launched']['A']['state'],'running')
            finally:
                os.kill(pid,signal.SIGTERM)
                for process in backend.LAUNCHED:
                    if process.pid==pid:process.wait(timeout=3)

class DiscoveryTests(unittest.TestCase):
    def test_unique_slot_candidate_is_suggested_but_ambiguous_candidates_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            a=home/'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
            b=home/'Library/Containers/local.chat-rescue.wechat.B/Data/Documents/xwechat_files'
            for path in [a/'wxid_a_1234/db_storage',a/'wxid_other_5678/db_storage',b/'wxid_b_abcd/db_storage']:
                for rel in backend.ops.ALL:
                    p=path/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'synthetic')
            with patch.object(backend.cli.Path,'home',return_value=home),patch.object(backend,'running_pid',return_value=None):
                found=backend.discover_accounts()
            self.assertIsNone(found['A']['suggestion'])
            self.assertEqual(found['B']['suggestion']['owner'],'wxid_b')

    def test_directory_name_is_suggestion_not_verified_identity(self):
        self.assertEqual(backend.owner_from_root('/fixture/wxid_abc_1234/db_storage'),'wxid_abc')

class CaptureReceiptTests(unittest.TestCase):
    def test_partial_key_file_never_counts_as_capture_success(self):
        from chat_rescue.workflow import private_json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);private_json(root/'capture-request.json',{'output':str(root/'keys.json')});private_json(root/'keys.json',{'keys':{}})
            result=backend.capture_status(root/'capture-request.json')
            self.assertEqual(result['state'],'waiting')
            private_json(root/'capture-result.json',{'state':'failed','error':'synthetic'})
            result=backend.capture_status(root/'capture-request.json');self.assertEqual(result['state'],'failed')

    def test_complete_verified_receipt_returns_ready_keys(self):
        from chat_rescue.workflow import private_json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);private_json(root/'capture-request.json',{'output':str(root/'keys.json')});private_json(root/'keys.json',{'keys':{}})
            private_json(root/'capture-result.json',{'state':'complete','verified_required_databases':4})
            result=backend.capture_status(root/'capture-request.json')
            self.assertEqual(result['state'],'complete');self.assertEqual(result['keys'],str(root/'keys.json'))

class CaptureWaitTests(unittest.TestCase):
    def test_abandoned_capture_times_out_and_unlocks(self):
        import time
        from chat_rescue.workflow import private_json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);private_json(root/'capture-request.json',{'output':str(root/'keys.json'),'timeout':10,'started_at':time.time()-600})
            result=backend.capture_status(root/'capture-request.json')
            self.assertEqual(result['state'],'failed')

    def test_closed_terminal_without_receipt_is_detected(self):
        from chat_rescue.workflow import private_json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);private_json(root/'capture-request.json',{'output':str(root/'keys.json'),'timeout':300})
            private_json(root/'capture-running.json',{'pid':99999999})
            result=backend.capture_status(root/'capture-request.json')
            self.assertEqual(result['state'],'failed')

class LegacyUpgradeTests(unittest.TestCase):
    def test_legacy_b_copy_is_preserved_and_replaced_by_independent_copy(self):
        import plistlib,platform,shutil,subprocess
        if platform.system()!='Darwin' or platform.machine()!='arm64':self.skipTest('macOS signing')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'original.app';exe=original/'Contents/MacOS/WeChat'
            exe.parent.mkdir(parents=True);shutil.copyfile('/usr/bin/true',exe);exe.chmod(0o755)
            (original/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.tencent.xinWeChat','CFBundleShortVersionString':'4.1.13','CFBundleExecutable':'WeChat','CFBundlePackageType':'APPL'}))
            subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(original)],check=True,capture_output=True)
            with patch.object(backend,'BASE',root/'support'),patch.object(backend.ops,'ensure_closed'),patch.object(backend.cli,'ensure_closed'):
                for slot in ('A','B'):prepare(original,backend.slot_app(slot),signing_id='local.chat-rescue.wechat.'+slot)
                retained=root/'original-chat.db';retained.write_bytes(b'existing history')
                self.assertFalse(backend.copy_ready('B'))
                backend.dispatch({'action':'prepare-both','original':str(original)})
                self.assertTrue(backend.copy_ready('B'))
                self.assertEqual(plistlib.loads((backend.slot_app('B')/'Contents/Info.plist').read_bytes())['CFBundleIdentifier'],'local.chat-rescue.wechat.B')
                self.assertTrue(list((root/'support/copy-revisions').glob('*/WeChat-B.app')))
                self.assertEqual(retained.read_bytes(),b'existing history')
