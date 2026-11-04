"""All-shard Mac inspection with synthetic encrypted databases only."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_cli
import test_capture_scope
from test_workflow import A, B
from chat_rescue import __main__ as cli, operations as ops, desktop_backend as desktop
from chat_rescue.shards import message_shards
from chat_rescue.vendor.sqlcipher_probe import DB
from chat_rescue.cipher import encrypt_database
from chat_rescue.workflow import digest, private_json, save_chat


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'message').mkdir()

    def touch(self, name):
        (self.root / 'message' / name).touch()

    def test_numeric_order_gaps_and_auxiliary_files(self):
        for name in ('message_10.db', 'message_2.db', 'message_0.db',
                     'message_resource.db', 'message_fts.db', 'biz_message_0.db',
                     'message_2.db-wal', 'message_2.db-shm'):
            self.touch(name)
        self.assertEqual(message_shards(self.root),
                         ('message/message_0.db', 'message/message_2.db', 'message/message_10.db'))

    def test_missing_zero_or_orphan_sidecar_rejected(self):
        self.touch('message_1.db')
        with self.assertRaisesRegex(ValueError, '缺少 message_0'):
            message_shards(self.root)
        self.touch('message_0.db'); self.touch('message_2.db-wal')
        with self.assertRaisesRegex(ValueError, '缺少数据库'):
            message_shards(self.root)

    def test_symlink_and_ambiguous_number_rejected(self):
        self.touch('message_0.db'); self.touch('message_01.db')
        with self.assertRaisesRegex(ValueError, '不受支持'):
            message_shards(self.root)
        (self.root / 'message/message_01.db').unlink()
        (self.root / 'message/message_1.db').symlink_to(self.root / 'message/message_0.db')
        with self.assertRaisesRegex(ValueError, '不受支持'):
            message_shards(self.root)


class EncryptedInspectionTests(unittest.TestCase):
    setUp = test_cli.CLITests.setUp
    tearDown = test_cli.CLITests.tearDown
    encrypted = test_cli.CLITests.encrypted
    capture = test_capture_scope.CaptureScopeTests.capture

    def extra_shard(self):
        root, keys = self.source
        rel = 'message/message_2.db'
        key = os.urandom(32).hex()
        with DB(root / rel, key, fixture_write=True) as database:
            database.query('CREATE TABLE template(id INTEGER);')
        values = json.loads(keys.read_text())
        values['keys'][rel] = {'enc_key': key}
        keys.write_text(json.dumps(values))
        candidate = self.root / 'extra.db'
        encrypt_database(self.root / (B + '-plain') / ops.ALL[0], candidate, root / rel, keys)
        os.replace(candidate, root / rel)
        return rel

    def test_verify_export_and_inspect_every_shard(self):
        rel = self.extra_shard()
        root, keys = self.source
        before = ops.state(root)
        with patch.object(ops.platform, 'system', return_value='Darwin'), \
             patch.object(cli, 'supported_host'), patch.object(cli, 'ensure_closed'), \
             patch.object(ops, 'ensure_closed'):
            result = cli.verify_keys(root, keys)
            self.assertEqual(result['verified_required_databases'], 5)
            self.assertIn(rel, result['verified_databases'])
            ops.export_root(root, keys, self.root / 'export')
            report = json.loads((self.root / 'export/export-report.json').read_text())
            self.assertEqual(set(report['exported']), set(ops.ALL) | {rel})
            self.assertEqual(report['originals'], before)
            self.assertTrue((self.root / 'export/_encrypted_snapshot' / rel).is_file())
            with patch.object(desktop, 'new_job', return_value=self.root / 'inspect'):
                view = desktop.inspect_account({'root': str(root), 'owner': B, 'keys': str(keys)})
            self.assertEqual(view['conversations'][0]['count'], 4)
            self.assertEqual(view['conversations'][0]['shards'], ['message/message_0.db', rel])
            self.assertFalse(view['recovery_supported'])
            with self.assertRaisesRegex(ValueError, '恢复写入仅支持'):
                save_chat(self.root / 'export', B, A, self.root / 'bundle')
        self.assertEqual(ops.state(root), before)

    def test_missing_extra_key_never_passes_verification(self):
        rel = self.extra_shard(); root, keys = self.source
        values = json.loads(keys.read_text()); del values['keys'][rel]
        keys.write_text(json.dumps(values))
        with patch.object(ops.platform, 'system', return_value='Darwin'), \
             patch.object(cli, 'supported_host'), patch.object(cli, 'ensure_closed'):
            with self.assertRaises((ValueError, KeyError, RuntimeError)):
                cli.verify_keys(root, keys)
            self.assertEqual(desktop.inspect_account({'root': str(root), 'owner': B, 'keys': str(keys)}),
                             {'requires_message_keys': True})

    def test_new_shard_during_export_invalidates_result(self):
        root, keys = self.source
        export_database = ops.export_database
        def changing_export(*args):
            export_database(*args)
            if not (root / 'message/message_9.db').exists():
                shutil.copy2(root / 'message/message_0.db', root / 'message/message_9.db')
        with patch.object(ops.platform, 'system', return_value='Darwin'), \
             patch.object(ops, 'ensure_closed'), patch.object(ops, 'export_database', side_effect=changing_export):
            with self.assertRaisesRegex(ValueError, '原库改变'):
                ops.export_root(root, keys, self.root / 'changed-export')
        self.assertFalse((self.root / 'changed-export/export-report.json').exists())

    def test_windows_scope_remains_restricted(self):
        self.extra_shard()
        with patch.object(ops.platform, 'system', return_value='Windows'):
            with self.assertRaisesRegex(ValueError, '仅支持单个'):
                ops.inspection_files(self.source[0])

    def test_capture_waits_for_every_shard_and_receipt_binds_inventory(self):
        rel = self.extra_shard()
        with patch.object(ops.platform, 'system', return_value='Darwin'):
            result = self.capture(5)
            self.assertEqual(result['verified_required_databases'], 5)
            self.assertIn(rel, result['verified_databases'][str(self.source[0].resolve())])
            payload = self.root / 'capture-request.json'
            private_json(payload, {'output': result['keys'], 'db_roots': [str(self.source[0])]})
            private_json(self.root / 'capture-result.json', {'state': 'complete', **result})
            self.assertEqual(desktop.capture_status(payload)['state'], 'complete')
            shutil.copy2(self.source[0] / rel, self.source[0] / 'message/message_9.db')
            self.assertEqual(desktop.capture_status(payload)['state'], 'failed')

    def test_four_key_capture_cannot_pass_with_five_required_databases(self):
        self.extra_shard()
        with patch.object(ops.platform, 'system', return_value='Darwin'):
            with self.assertRaises(RuntimeError):
                self.capture(4)
