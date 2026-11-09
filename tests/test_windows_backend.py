"""Desktop bridge guards tested with synthetic manifests; never touches WeChat."""
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from chat_rescue import windows_backend as backend
from chat_rescue.workflow import private_json, digest


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.plan = self.root / 'plan'; self.plan.mkdir()
        private_json(self.plan / 'sync-plan.json', {'version': 1,
                     'accounts': {'A': {'owner': 'synthetic_a'}, 'B': {'owner': 'synthetic_b'}},
                     'directions': {'A': {'added': 3, 'skipped': 0}, 'B': {'added': 0, 'skipped': 3}}})

    def apply_request(self, **extras):
        return {'action': 'apply', 'plan': str(self.plan),
                'plan_hash': digest(self.plan / 'sync-plan.json'), 'confirmed': True, **extras}

    def test_confirmation_required_before_application(self):
        with patch.object(backend, 'apply_sync_plan') as apply:
            with self.assertRaises(ValueError): backend.dispatch(self.apply_request(confirmed=False))
            apply.assert_not_called()

    def test_changed_plan_blocks_application(self):
        request = self.apply_request()
        (self.plan / 'sync-plan.json').write_text(json.dumps({'version': 1, 'accounts': {'A': {}, 'B': {}}, 'directions': {}}), encoding='utf-8')
        with patch.object(backend, 'apply_sync_plan') as apply:
            with self.assertRaises(ValueError): backend.dispatch(request)
            apply.assert_not_called()

    def test_interrupted_partial_and_applied_tasks_cannot_repeat(self):
        for state in ('partial', 'failed', 'applied'):
            receipt = self.plan / 'sync-result.json'
            receipt.write_text(json.dumps({'state': state}), encoding='utf-8')
            with patch.object(backend, 'apply_sync_plan') as apply:
                with self.assertRaises(ValueError): backend.dispatch(self.apply_request())
                apply.assert_not_called()
        receipt.unlink()
        private_json(self.plan / 'sync-started.json', {'state': 'started'})
        with patch.object(backend, 'apply_sync_plan') as apply:
            with self.assertRaises(ValueError): backend.dispatch(self.apply_request())
            apply.assert_not_called()

    def test_confirmed_ready_plan_uses_existing_coordinated_apply(self):
        with patch.object(backend, 'apply_sync_plan', return_value={'state': 'applied'}) as apply:
            self.assertEqual(backend.dispatch(self.apply_request())['state'], 'applied')
            apply.assert_called_once_with(str(self.plan.resolve()))

    def test_client_confirmation_cannot_precede_database_application(self):
        request = {**self.apply_request(), 'action': 'confirm-client'}
        with self.assertRaises(ValueError): backend.dispatch(request)
        private_json(self.plan / 'sync-result.json', {'state': 'applied'})
        result = backend.dispatch(request)
        self.assertEqual(result, {'state': 'user_confirmed', 'phone_verified': False})
        self.assertTrue(backend.plan_view(self.plan)['client_confirmed'])

    def test_undo_requires_full_application_and_confirmation(self):
        request = {**self.apply_request(), 'action': 'undo'}
        with patch.object(backend, 'undo_sync_plan') as undo:
            with self.assertRaises(ValueError): backend.dispatch(request)
            undo.assert_not_called()
        private_json(self.plan / 'sync-result.json', {'state': 'applied'})
        with patch.object(backend, 'undo_sync_plan', return_value={'state': 'undone'}) as undo:
            with self.assertRaises(ValueError): backend.dispatch({**request, 'confirmed': False})
            self.assertEqual(backend.dispatch(request)['state'], 'undone')
            undo.assert_called_once_with(str(self.plan.resolve()))

    def test_simulation_report_never_returns_plan_or_key_material(self):
        path = self.root / 'simulation-report.json'
        private_json(path, {'simulation_only': True, 'keys': 'synthetic-secret',
                           'prepared_experiment': {'state': 'passed', 'added': 3, 'deduplicated_on_repeat': 3, 'plan': 'synthetic-plan'},
                           'original_databases_unchanged': {'A': True, 'B': True}})
        result = backend.dispatch({'action': 'simulation-report', 'path': str(path)})
        self.assertTrue(result['simulation_only'])
        self.assertNotIn('keys', result); self.assertNotIn('plan', result)
        self.assertFalse(result['client_display_verified'])

    def test_failed_simulation_cannot_be_presented_as_success(self):
        path = self.root / 'simulation-report.json'
        private_json(path, {'simulation_only': True, 'prepared_experiment': {'state': 'failed', 'added': 3}})
        with self.assertRaises(ValueError): backend.dispatch({'action': 'simulation-report', 'path': str(path)})

    def test_arbitrary_import_slot_cannot_escape_task_directory(self):
        path = self.root / 'verified-accounts.json'
        private_json(path, {'accounts': [{'slot': '../other'}, {'slot': 'account-2'}]})
        with self.assertRaises(ValueError): backend.dispatch({'action': 'import-accounts', 'path': str(path)})

    def test_ambiguous_process_binding_blocks_capture(self):
        with patch.object(backend, 'account', return_value={'root': 'synthetic', 'owner': 'synthetic_owner'}), \
             patch.object(backend, 'resource_processes', return_value=[10, 11]), \
             patch.object(backend, 'main_processes', return_value=[10, 11]), \
             patch.object(backend, 'capture') as capture:
            with self.assertRaises(ValueError):
                backend.dispatch({'action': 'capture', 'account': {}, 'identity_confirmed': True, 'executable': 'synthetic.exe'})
            capture.assert_not_called()

    def test_read_uses_unique_client_pid_without_binding_account_to_file_holders(self):
        selected={'root':'synthetic-root','owner':'synthetic-owner','keys':''}
        with patch.object(backend,'account',return_value=selected), \
             patch.object(backend,'main_processes',return_value=[123]), \
             patch.object(backend,'resource_processes',side_effect=AssertionError('no account/process binding')), \
             patch.object(backend,'job',return_value=self.root), \
             patch.object(backend,'capture',return_value={'keys':'synthetic-keys','state':'verified'}) as capture, \
             patch.object(backend,'account_nickname',return_value='Synthetic'):
            result=backend.dispatch({'action':'capture','account':{},'identity_confirmed':True,'executable':'synthetic.exe'})
        self.assertEqual(result['account']['nickname'],'Synthetic')
        self.assertEqual(capture.call_args.args[:4],(123,'synthetic.exe','synthetic-root','synthetic-owner'))

    @unittest.skipUnless(os.name == 'nt', 'Windows Restart Manager')
    def test_process_binding_finds_synthetic_wal_and_shm_holders(self):
        root = self.root / 'synthetic-db-storage'
        for rel in ('message/message_0.db', 'message/message_resource.db',
                    'session/session.db', 'contact/contact.db'):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic fixture')
        for suffix in ('-wal', '-shm'):
            with self.subTest(suffix=suffix):
                sidecar = root / ('message/message_0.db' + suffix)
                with sidecar.open('w+b') as stream:
                    stream.write(b'synthetic fixture')
                    stream.flush()
                    self.assertIn(os.getpid(), backend.resource_processes(root))

    def test_empty_target_error_is_actionable_json_not_traceback(self):
        output = io.StringIO()
        with patch.object(backend.sys, 'stdin', io.StringIO('{}')), patch.object(backend.sys, 'stdout', output), \
             patch.object(backend, 'dispatch', side_effect=sqlite3.OperationalError('no such table: Name2Id')):
            self.assertEqual(backend.main(), 2)
        result = json.loads(output.getvalue())
        self.assertFalse(result['ok']); self.assertIn('空白账号', result['error'])

    def test_invalid_request_encoding_returns_product_message(self):
        output = io.StringIO()
        error = UnicodeDecodeError('utf-8', b'\xb0', 0, 1, 'invalid start byte')
        with patch.object(backend.sys, 'stdout', output), patch.object(backend.json, 'load', side_effect=error):
            self.assertEqual(backend.main(), 2)
        result = json.loads(output.getvalue())
        self.assertFalse(result['ok']); self.assertIn('字符编码', result['error'])
        self.assertNotIn('0xb0', result['error'])

    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(ValueError): backend.dispatch({'action': 'execute-shell'})

    def test_launch_requires_slot_and_starts_exactly_one_process(self):
        with patch.object(backend, 'app_info', return_value={'executable': 'synthetic.exe'}), \
             patch.object(backend, 'ensure_closed') as closed, patch.object(backend.subprocess, 'Popen') as launch:
            with self.assertRaises(ValueError): backend.dispatch({'action': 'launch', 'executable': 'synthetic.exe'})
            result = backend.dispatch({'action': 'launch', 'slot': 'A', 'executable': 'synthetic.exe'})
            self.assertEqual(result['slot'], 'A'); launch.assert_called_once(); closed.assert_called_once()

    def test_running_wechat_blocks_second_launch(self):
        with patch.object(backend, 'app_info', return_value={'executable': 'synthetic.exe'}), \
             patch.object(backend, 'ensure_closed', side_effect=RuntimeError('still running')), \
             patch.object(backend.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, 'still running'):
                backend.dispatch({'action': 'launch', 'slot': 'B', 'executable': 'synthetic.exe'})
            launch.assert_not_called()

    def test_source_can_be_launched_without_reading_target_first(self):
        with patch.object(backend, 'app_info', return_value={'executable': 'synthetic.exe'}), \
             patch.object(backend, 'ensure_closed'), patch.object(backend, 'account', return_value={'keys': 'synthetic.json', 'root': 'synthetic'}), \
             patch.object(backend, 'require_private'), patch.object(backend.cli, 'verify_keys') as verify, \
             patch.object(backend.subprocess, 'Popen') as launch:
            backend.dispatch({'action': 'launch', 'slot': 'B', 'executable': 'synthetic.exe', 'a': {}})
            launch.assert_called_once();verify.assert_not_called()

    def test_closed_wechat_reuses_verified_existing_keys_without_capture(self):
        selected = {'root': 'synthetic', 'owner': 'synthetic_owner', 'keys': 'synthetic.json'}
        with patch.object(backend, 'account', return_value=selected), patch.object(backend, 'main_processes', return_value=[]), \
             patch.object(backend, 'ensure_closed'), patch.object(backend, 'require_private'), \
             patch.object(backend.cli, 'verify_keys') as verify, patch.object(backend, 'account_nickname', return_value='合成昵称'), \
             patch.object(backend, 'capture') as capture:
            result = backend.dispatch({'action': 'capture', 'account': {}, 'identity_confirmed': True, 'executable': 'synthetic.exe'})
            self.assertEqual(result['account']['nickname'], '合成昵称')
            verify.assert_called_once_with('synthetic', 'synthetic.json'); capture.assert_not_called()

    def test_zero_processes_without_keys_does_not_claim_multiple_instances(self):
        with patch.object(backend,'account',return_value={'root':'synthetic','owner':'synthetic','keys':''}), \
                patch.object(backend,'main_processes',return_value=[]):
            with self.assertRaisesRegex(ValueError,'0 个.*未找到'):
                backend.dispatch({'action':'capture','account':{},'identity_confirmed':True,'executable':'synthetic.exe'})

    def test_multiple_matching_processes_report_count_and_pids(self):
        with patch.object(backend,'account',return_value={'root':'synthetic','owner':'synthetic','keys':''}), \
                patch.object(backend,'main_processes',return_value=[42,84]):
            with self.assertRaisesRegex(ValueError,'2 个.*42.*84'):
                backend.dispatch({'action':'capture','account':{},'identity_confirmed':True,'executable':'synthetic.exe'})

    def test_settings_save_paths_without_secrets_or_confirmation(self):
        values = [{'owner': 'synthetic', 'root': 'synthetic-root', 'keys': 'synthetic-path.json',
                   'enc_key': 'never-persist-key-data', 'confirmed': True} for _ in range(2)]
        with patch.object(backend, 'workspace', return_value=self.root):
            backend.dispatch({'action': 'save-settings', 'accounts': values})
            settings = backend.saved_settings()
        self.assertNotIn('enc_key', settings['accounts'][0]); self.assertNotIn('confirmed', settings['accounts'][0])
        self.assertEqual(settings['accounts'][0]['keys'], 'synthetic-path.json')

    def test_separate_workspace_starts_empty_and_preserves_prior_settings(self):
        old, new = self.root / 'old-profile', self.root / 'new-profile'
        with patch.dict(os.environ, {'CHAT_RESCUE_WORKSPACE': str(old)}):
            backend.dispatch({'action': 'save-settings', 'accounts': [{'nickname': '合成旧账号', 'keys': 'synthetic.json'}, {}]})
            before = (old / 'settings.json').read_bytes()
        with patch.dict(os.environ, {'CHAT_RESCUE_WORKSPACE': str(new)}):
            self.assertEqual(backend.workspace(), new.resolve())
            self.assertEqual(backend.saved_settings(), {})
            self.assertFalse((new / 'jobs').exists())
        self.assertEqual((old / 'settings.json').read_bytes(), before)

    def test_relative_workspace_is_refused(self):
        with patch.dict(os.environ, {'CHAT_RESCUE_WORKSPACE': 'relative-profile'}):
            with self.assertRaises(ValueError): backend.workspace()

    def test_discovery_uses_only_current_workspace_labels(self):
        history = self.root / 'ChatRescue/win-real-test-old'
        history.mkdir(parents=True)
        private_json(history / 'verified-accounts.json', {'accounts': [{'owner': 'synthetic_owner',
                     'root': str(self.root / 'db_storage'),
                     'contacts': [{'username': 'synthetic_owner', 'nick_name': '旧实验昵称'}]}]})
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root), 'APPDATA': str(self.root / 'config'),
                                    'CHAT_RESCUE_WORKSPACE': str(self.root / 'fresh-profile')}), \
             patch.object(backend.cli, 'roots', return_value={'database_roots': [str(self.root / 'db_storage')]}):
            self.assertNotIn('旧实验昵称', backend.discover()[0]['label'])
            backend.save_settings({'accounts': [{'root': str(self.root / 'db_storage'), 'nickname': '本次昵称'}, {}]})
            self.assertEqual(backend.discover()[0]['label'], '本次昵称 · 上次识别 · 路径已失效')
