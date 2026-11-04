"""Multi-shard regression fixtures contain synthetic identities/messages only."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_workflow import fixture, db, A, B, FIELDS
from chat_rescue.workflow import save_chat, prepare_plain_restore, table_for, unpack
from chat_rescue import operations as ops
from chat_rescue.sync import conversation_count
from chat_rescue.cipher import encrypt_database, export_database
from chat_rescue.vendor.sqlcipher_probe import DB
from chat_rescue.workflow import private_json, digest
from chat_rescue import __main__ as cli
from chat_rescue import sync, windows_capture
from chat_rescue.shards import receipt_covers
import test_cli
import test_apply


def setUpModule():
    if os.name != 'nt':
        raise unittest.SkipTest('Windows multi-shard recovery scope')


def split_source(root):
    """Put sender B in shard 2, with different rowids and a repeated local_id."""
    second = root / 'message/message_2.db'
    shutil.copy2(root / 'message/message_0.db', second)
    with db(second) as c:
        c.execute('DELETE FROM ' + table_for(A) + ' WHERE server_id=101')
        c.execute('UPDATE Name2Id SET rowid=70 WHERE user_name=?', (A,))
        c.execute('UPDATE Name2Id SET rowid=90 WHERE user_name=?', (B,))
        c.execute('UPDATE ' + table_for(A) + ' SET local_id=1,real_sender_id=90,packed_info_data=?',
                  (bytes.fromhex('085a1001'),))
    with db(root / 'message/message_0.db') as c:
        c.execute('DELETE FROM ' + table_for(A) + ' WHERE server_id=102')


class ShardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.target = self.root/'source', self.root/'target'
        fixture(self.source, True)
        fixture(self.target, False)
        self.bundle = self.root/'bundle'

    def save(self):
        return save_chat(self.source, B, A, self.bundle)

    def test_save_reads_each_shards_identity_mapping_and_keeps_server_ids(self):
        split_source(self.source)
        self.assertEqual(self.save()['saved'], 2)
        payload = unpack(json.loads((self.bundle/'conversation.json').read_text(encoding='utf-8')))
        self.assertEqual([(m['fields']['server_id'], m['sender']) for m in payload['messages']],
                         [(101, A), (102, B)])
        self.assertEqual(payload['source_shards'], ['message/message_0.db', 'message/message_2.db'])
        result = prepare_plain_restore(self.bundle, self.target, A, self.root/'result')
        with db(self.root/'result/message/message_0.db') as c:
            rows = c.execute('SELECT server_id,real_sender_id,hex(packed_info_data) FROM ' +
                             result['table'] + ' ORDER BY server_id').fetchall()
        self.assertEqual(rows, [(101, 4, '080410013A0178'), (102, 302, '08AE021002')])

    def test_conversation_only_in_nonzero_shard_is_counted_and_saved(self):
        (self.source/'message/message_0.db').rename(self.source/'message/message_10.db')
        shutil.copy2(self.target/'message/message_0.db', self.source/'message/message_0.db')
        self.assertEqual(conversation_count(self.source, A), 2)
        self.assertEqual(self.save()['saved'], 2)

    def test_snapshot_tracks_every_shard_and_sidecar(self):
        split_source(self.source)
        wal = self.source/'message/message_2.db-wal'
        wal.write_bytes(b'synthetic sidecar')
        state = ops.state(self.source)
        self.assertIn('message/message_2.db', state)
        self.assertIsNotNone(state['message/message_2.db-wal'])
        self.assertIsNone(state['message/message_2.db-shm'])

    def test_verify_keys_allows_its_own_shm_index_refresh_but_not_db_changes(self):
        shm = self.source/'message/message_0.db-shm'
        for refresh, allowed in ((shm, True), (self.source/'message/message_0.db', False)):
            with self.subTest(allowed=allowed), patch.object(cli, 'ensure_closed'), \
                    patch.object(cli, 'load_key', return_value='00'*32), patch.object(cli, 'DB') as provider:
                def query(sql):
                    refresh.write_bytes(b'synthetic reader refresh')
                    return [{'integrity_check': 'ok'}]
                provider.return_value.__enter__.return_value.query.side_effect = query
                if allowed:
                    self.assertEqual(cli.verify_keys(self.source, 'unused')['verified_required_databases'], 4)
                else:
                    with self.assertRaises(ValueError):
                        cli.verify_keys(self.source, 'unused')

    def test_restore_targets_existing_conversation_in_nonzero_shard(self):
        self.save()
        prepare_plain_restore(self.bundle, self.target, A, self.root/'first')
        first = self.root/'first'
        (first/'message/message_0.db').rename(first/'message/message_2.db')
        shutil.copy2(self.target/'message/message_0.db', first/'message/message_0.db')
        with db(first/'message/message_2.db') as c:
            c.execute('DELETE FROM ' + table_for(B) + ' WHERE server_id=102')
        original = (first/'message/message_0.db').read_bytes()
        result = prepare_plain_restore(self.bundle, first, A, self.root/'result')
        self.assertEqual((result['added'], result['skipped']), (1, 1))
        self.assertEqual(result['target_message_shard'], 'message/message_2.db')
        self.assertEqual((self.root/'result/message/message_0.db').read_bytes(), original)
        with db(self.root/'result/message/message_2.db') as c:
            self.assertEqual(c.execute('SELECT server_id FROM ' + table_for(B) +
                                      ' ORDER BY server_id').fetchall(), [(101,), (102,)])

    def test_global_dedup_uses_identity_in_each_target_shard(self):
        self.save()
        prepare_plain_restore(self.bundle, self.target, A, self.root/'first')
        first = self.root/'first'
        shutil.copy2(first/'message/message_0.db', first/'message/message_2.db')
        with db(first/'message/message_0.db') as c:
            c.execute('DELETE FROM ' + table_for(B) + ' WHERE server_id=102')
        with db(first/'message/message_2.db') as c:
            c.execute('DELETE FROM ' + table_for(B) + ' WHERE server_id=101')
            c.execute('UPDATE Name2Id SET rowid=70 WHERE user_name=?', (A,))
            c.execute('UPDATE Name2Id SET rowid=90 WHERE user_name=?', (B,))
            c.execute('UPDATE ' + table_for(B) + ' SET real_sender_id=90,packed_info_data=?',
                      (bytes.fromhex('085a1002'),))
        result = prepare_plain_restore(self.bundle, first, A, self.root/'result')
        self.assertEqual((result['added'], result['skipped']), (0, 2))
        with db(self.root/'result/session/session.db') as c:
            self.assertEqual(c.execute('SELECT last_msg_locald_id FROM SessionTable').fetchone()[0], 2)

    def test_collision_in_other_shard_conversation_is_refused(self):
        self.save()
        shutil.copy2(self.source/'message/message_0.db', self.target/'message/message_2.db')
        with self.assertRaisesRegex(ValueError, 'collision in another conversation'):
            prepare_plain_restore(self.bundle, self.target, A, self.root/'result')

    def test_nonstandard_msg_table_is_not_silently_excluded_from_collision_scan(self):
        self.save()
        shutil.copy2(self.source/'message/message_0.db', self.target/'message/message_2.db')
        with db(self.target/'message/message_2.db') as c:
            c.execute('ALTER TABLE ' + table_for(A) + ' RENAME TO Msg_future_format')
        with self.assertRaisesRegex(ValueError, 'collision in another conversation'):
            prepare_plain_restore(self.bundle, self.target, A, self.root/'result')

    def test_collision_in_nonzero_shard_is_not_overwritten(self):
        self.save()
        prepare_plain_restore(self.bundle, self.target, A, self.root/'first')
        first = self.root/'first'
        (first/'message/message_0.db').rename(first/'message/message_2.db')
        shutil.copy2(self.target/'message/message_0.db', first/'message/message_0.db')
        with db(first/'message/message_2.db') as c:
            c.execute('UPDATE ' + table_for(B) + ' SET message_content=? WHERE server_id=102', ('changed',))
        with self.assertRaisesRegex(ValueError, 'collision'):
            prepare_plain_restore(self.bundle, first, A, self.root/'result')

    def test_identical_source_duplicate_is_deduplicated_but_conflict_refused(self):
        shutil.copy2(self.source/'message/message_0.db', self.source/'message/message_2.db')
        self.assertEqual(self.save()['saved'], 2)
        with db(self.source/'message/message_2.db') as c:
            c.execute('UPDATE ' + table_for(A) + ' SET message_content=? WHERE server_id=101', ('changed',))
        with self.assertRaisesRegex(ValueError, 'collision|conflict'):
            save_chat(self.source, B, A, self.root/'conflict')

    def test_target_without_conversation_uses_highest_numeric_shard(self):
        self.save()
        for index in (2, 10):
            shutil.copy2(self.target/'message/message_0.db', self.target/f'message/message_{index}.db')
        result = prepare_plain_restore(self.bundle, self.target, A, self.root/'result')
        self.assertEqual(result['target_message_shard'], 'message/message_10.db')
        self.assertEqual(conversation_count(self.root/'result', B), 2)

    def test_different_message_schema_across_source_shards_is_refused(self):
        split_source(self.source)
        with db(self.source/'message/message_2.db') as c:
            c.execute('ALTER TABLE ' + table_for(A) + ' ADD COLUMN unknown_shard_field INTEGER')
        with self.assertRaisesRegex(ValueError, 'schema'):
            self.save()

    def test_orphan_sidecar_is_refused_instead_of_ignored(self):
        (self.source/'message/message_9.db-wal').write_bytes(b'orphan')
        with self.assertRaisesRegex(ValueError, 'orphan'):
            ops.state(self.source)

    def test_export_report_cannot_omit_a_discovered_shard(self):
        split_source(self.source)
        private_json(self.source/'export-report.json', {
            'version': 1, 'account_directory': B,
            'exported': {rel: digest(self.source/rel) for rel in ops.ALL}})
        with self.assertRaisesRegex(ValueError, '清单|inventory'):
            self.save()

    def test_new_local_ids_do_not_overlap_same_conversation_in_another_shard(self):
        self.save()
        prepare_plain_restore(self.bundle, self.target, A, self.root/'first')
        first = self.root/'first'
        shutil.copy2(first/'message/message_0.db', first/'message/message_2.db')
        with db(first/'message/message_0.db') as c:
            c.execute('DELETE FROM ' + table_for(B) + ' WHERE server_id=102')
            c.execute('UPDATE ' + table_for(B) + ' SET local_id=500')
        with db(first/'message/message_2.db') as c:
            c.execute('DELETE FROM ' + table_for(B))
        prepare_plain_restore(self.bundle, first, A, self.root/'result')
        with db(self.root/'result/message/message_2.db') as c:
            self.assertEqual(c.execute('SELECT local_id FROM ' + table_for(B)).fetchone()[0], 501)

    def test_source_foreign_resource_cache_is_not_exported(self):
        with db(self.source/'message/message_resource.db') as c:
            c.execute('INSERT INTO ChatName2Id(rowid,user_name,update_time) VALUES(99,?,0)', ('wxid_foreign_demo',))
            c.execute('INSERT INTO MessageResourceInfo VALUES(9,99,13,1,1700000000,1,101,6,?)', (b'foreign cache',))
        self.save()
        payload = unpack(json.loads((self.bundle/'conversation.json').read_text(encoding='utf-8')))
        self.assertEqual(len(payload['resources']), 1)
        self.assertEqual(payload['resources'][0]['info']['chat_id'], 11)

    def test_receipt_requires_exact_nonzero_shard_coverage(self):
        split_source(self.source)
        self.assertFalse(receipt_covers(self.source, {'verified_required_databases': 4}))
        self.assertFalse(receipt_covers(self.source, {'verified_required_databases': 5}))
        self.assertFalse(receipt_covers(self.source, {'verified_required_databases': 5,
                         'verified_databases': [*ops.ALL, 'message/message_9.db']}))
        self.assertTrue(receipt_covers(self.source, {'verified_required_databases': 5,
                        'verified_databases': [*ops.ALL, 'message/message_2.db']}))

    def test_desktop_conversation_list_reads_nonzero_shard(self):
        from chat_rescue import desktop_backend
        split_source(self.source)
        job = self.root/'job'
        def export(root, keys, output):
            shutil.copytree(self.source, output)
        with patch.object(desktop_backend, 'new_job', return_value=job), \
                patch.object(ops, 'check_owner'), patch.object(ops, 'export_root', side_effect=export), \
                patch('chat_rescue.vendor.sqlcipher_probe.load_key', return_value='00'*32):
            result = desktop_backend.inspect_account({'root': str(self.source), 'owner': B, 'keys': 'unused'})
        self.assertEqual([(row['username'], row['count']) for row in result['conversations']], [(A, 2)])

    def test_identical_target_duplicates_with_different_mapping_deduplicate_once(self):
        self.save()
        first = self.root/'first'
        prepare_plain_restore(self.bundle, self.target, A, first)
        shutil.copy2(first/'message/message_0.db', first/'message/message_2.db')
        with db(first/'message/message_2.db') as c:
            c.execute('UPDATE Name2Id SET rowid=70 WHERE user_name=?', (A,))
            c.execute('UPDATE Name2Id SET rowid=90 WHERE user_name=?', (B,))
            c.execute('UPDATE ' + table_for(B) + ' SET real_sender_id=70,packed_info_data=? WHERE server_id=101',
                      (bytes.fromhex('084610013a0178'),))
            c.execute('UPDATE ' + table_for(B) + ' SET real_sender_id=90,packed_info_data=? WHERE server_id=102',
                      (bytes.fromhex('085a1002'),))
        result = prepare_plain_restore(self.bundle, first, A, self.root/'result')
        self.assertEqual((result['added'], result['skipped']), (0, 2))
        with db(first/'message/message_2.db') as c:
            c.execute('UPDATE ' + table_for(B) + ' SET message_content=? WHERE server_id=102', ('conflict',))
        with self.assertRaisesRegex(ValueError, 'collision'):
            prepare_plain_restore(self.bundle, first, A, self.root/'conflict')

    def test_undo_rejects_traversal_and_incomplete_manifest_before_file_changes(self):
        accounts, after = {}, {}
        for owner in (A, B):
            root = self.root/owner/'db_storage'
            root.parent.mkdir()
            fixture(root, owner == B)
            accounts[owner] = {'root': str(root), 'owner': owner}
            after[owner] = ops.state(root)
        directory = self.root/'undo'; directory.mkdir()
        victim = self.root/A/'victim.txt'; victim.write_text('must survive')
        private_json(directory/'sync-plan.json', {'version': 1,
            'accounts': {'A': accounts[A], 'B': accounts[B]},
            'originals': {'A': {'../victim.txt': None}, 'B': {}}})
        private_json(directory/'sync-result.json', {'state': 'applied',
            'accounts': {'A': {'state': 'applied', 'after': after[A]},
                         'B': {'state': 'unchanged', 'after': after[B]}}})
        with patch('chat_rescue.operations.ensure_closed'):
            with self.assertRaises(ValueError):
                sync.undo_sync_plan(directory)
        self.assertTrue(victim.exists())
        self.assertFalse((directory/'undo-started.json').exists())


class MultiShardEncryptedTests(unittest.TestCase):
    setUp = test_cli.CLITests.setUp
    tearDown = test_cli.CLITests.tearDown
    encrypted = test_cli.CLITests.encrypted

    def install(self, pair, plain):
        live, keys = pair
        manifest = json.loads(keys.read_text(encoding='utf-8'))['keys']
        for path in (plain/'message').glob('message_*.db'):
            if path.stem.rsplit('_', 1)[-1].isdigit():
                rel = 'message/' + path.name
                if rel not in manifest:
                    key = os.urandom(32).hex()
                    manifest[rel] = {'enc_key': key}
                    with DB(live/rel, key, fixture_write=True) as c:
                        c.query('CREATE TABLE template(id INTEGER PRIMARY KEY);')
        keys.write_text(json.dumps({'keys': manifest}), encoding='utf-8')
        for rel in manifest:
            candidate = Path(str(live/rel) + '.candidate')
            encrypt_database(plain/rel, candidate, live/rel, keys)
            candidate.replace(live/rel)

    def setup_shards(self):
        split_source(self.root/(B+'-plain'))
        self.install(self.source, self.root/(B+'-plain'))
        plain = self.root/(A+'-plain')
        shutil.copy2(plain/'message/message_0.db', plain/'message/message_2.db')
        self.install(self.target, plain)

    def make_plan(self):
        self.setup_shards()
        with patch('chat_rescue.operations.ensure_closed'):
            ops.export_root(*self.source, self.root/'export')
            save_chat(self.root/'export', B, A, self.root/'bundle')
            return ops.make_plan(self.root/'bundle', *self.target, A, self.root/'plan')

    def test_encrypted_export_plan_apply_and_reexport_cover_all_shards(self):
        result = self.make_plan()
        self.assertEqual(result['target_message_shard'], 'message/message_2.db')
        with patch('chat_rescue.__main__.ensure_closed'):
            verified = cli.verify_keys(*self.source)
        self.assertEqual(verified['verified_required_databases'], 5)
        before = ops.state(self.target[0])
        with patch('chat_rescue.operations.ensure_closed'):
            self.assertEqual(ops.apply_plan(self.root/'plan')['state'], 'applied')
            ops.export_root(*self.target, self.root/'read')
        self.assertEqual(conversation_count(self.root/'read', B), 2)
        for rel, value in before.items():
            if value is not None:
                self.assertEqual(digest(self.root/'plan/originals'/rel), value)
        with db(self.root/'read/message/message_2.db') as c:
            self.assertEqual(c.execute('SELECT server_id,real_sender_id FROM ' + table_for(B) +
                                      ' ORDER BY server_id').fetchall(), [(101, 4), (102, 302)])

    def test_missing_nonzero_shard_key_is_refused(self):
        self.setup_shards()
        keys = self.source[1]
        payload = json.loads(keys.read_text(encoding='utf-8'))
        payload['keys'].pop('message/message_2.db')
        keys.write_text(json.dumps(payload), encoding='utf-8')
        with patch('chat_rescue.operations.ensure_closed'):
            with self.assertRaises((ValueError, KeyError, RuntimeError)):
                ops.export_root(*self.source, self.root/'export')
        self.assertFalse((self.root/'export/export-report.json').exists())

    def test_new_shard_after_plan_blocks_application(self):
        self.make_plan()
        shutil.copy2(self.target[0]/'message/message_0.db', self.target[0]/'message/message_10.db')
        before = ops.state(self.target[0])
        with patch('chat_rescue.operations.ensure_closed'):
            with self.assertRaises(ValueError):
                ops.apply_plan(self.root/'plan')
        self.assertEqual(ops.state(self.target[0]), before)

    def test_nonzero_wal_change_blocks_application(self):
        self.make_plan()
        (self.target[0]/'message/message_2.db-wal').write_bytes(b'changed')
        with patch('chat_rescue.operations.ensure_closed'):
            with self.assertRaises(ValueError):
                ops.apply_plan(self.root/'plan')
        self.assertFalse((self.root/'plan/apply-started.json').exists())

    def test_partial_replace_restores_every_shard_and_sidecar(self):
        self.make_plan()
        before = ops.state(self.target[0])
        real = os.replace
        def fail_resource(source, destination):
            if str(source).endswith('message_resource.db.chat-rescue-stage'):
                raise OSError('synthetic third database write failure')
            return real(source, destination)
        with patch('chat_rescue.operations.ensure_closed'), patch('chat_rescue.operations.os.replace', side_effect=fail_resource):
            with self.assertRaises(OSError):
                ops.apply_plan(self.root/'plan')
        self.assertEqual(ops.state(self.target[0]), before)
        self.assertEqual(json.loads((self.root/'plan/rollback.json').read_text())['state'], 'rolled_back')

    def test_multishard_sync_apply_and_undo_restore_exact_originals(self):
        self.setup_shards()
        accounts = [{'root': str(pair[0]), 'keys': str(pair[1]), 'owner': owner}
                    for pair, owner in ((self.target, A), (self.source, B))]
        before = [ops.state(pair[0]) for pair in (self.target, self.source)]
        with patch('chat_rescue.operations.ensure_closed'):
            result = sync.make_sync_plan(*accounts, self.root/'sync')
            self.assertEqual(result['directions']['A']['added'], 2)
            self.assertEqual(sync.apply_sync_plan(self.root/'sync')['state'], 'applied')
            self.assertEqual(sync.undo_sync_plan(self.root/'sync')['state'], 'undone')
        self.assertEqual([ops.state(pair[0]) for pair in (self.target, self.source)], before)

    def test_plan_path_traversal_is_refused_before_backup(self):
        self.make_plan()
        plan = self.root/'plan/plan.json'
        value = json.loads(plan.read_text(encoding='utf-8'))
        value['originals']['../outside.db'] = 'synthetic'
        plan.write_text(json.dumps(value), encoding='utf-8')
        with patch('chat_rescue.operations.ensure_closed'):
            with self.assertRaises(ValueError):
                ops.apply_plan(self.root/'plan')
        self.assertFalse((self.root/'plan/originals').exists())


class MultiShardCaptureTests(unittest.TestCase):
    def test_windows_capture_requires_all_shards_and_records_exact_list(self):
        from test_windows_capture import CaptureTests
        helper = CaptureTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        extra = helper.root/'message/message_2.db'
        extra.write_bytes(helper.memory.pages['message/message_0.db'])
        keys = {**helper.memory.keys, 'message/message_2.db': helper.memory.keys['message/message_0.db']}
        metadata = {'executable': str(helper.exe), 'sha256': digest(helper.exe), 'file_version': '4.2.0.12'}
        with patch.object(windows_capture, 'app_info', return_value=metadata), \
                patch.object(windows_capture, 'Process'), patch.object(windows_capture, 'scan', return_value=keys), \
                patch.object(windows_capture, 'require_private'), \
                patch('chat_rescue.vendor.sqlcipher_probe.DB') as database:
            database.return_value.__enter__.return_value.query.return_value = [{'integrity_check': 'ok'}]
            result = windows_capture.capture(123, helper.exe, helper.root, 'synthetic_owner', helper.output)
        receipt = json.loads(helper.output.with_suffix('.capture.json').read_text(encoding='utf-8'))
        self.assertEqual(result['verified_required_databases'], 5)
        self.assertTrue(receipt_covers(helper.root, receipt))
        self.assertEqual(set(json.loads(helper.output.read_text())['keys']), set(keys))


class ShardApplySafetyTests(unittest.TestCase):
    setUp = test_apply.ApplyTests.setUp
    tearDown = test_apply.ApplyTests.tearDown
    run_apply = test_apply.ApplyTests.run_apply

    def test_candidate_parent_symlink_is_rejected_before_any_write(self):
        directory = self.plan/'candidates/message'
        outside = self.root/'outside-candidates'
        directory.rename(outside)
        try:
            directory.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows symlink privilege unavailable')
            raise
        with self.assertRaises(ValueError):
            self.run_apply()
        self.assertEqual((self.live/'message/message_0.db').read_bytes(), b'old')

    def test_multishard_failure_restores_nonzero_db_wal_and_shm(self):
        rel = 'message/message_2.db'
        for suffix, value in (('', b'old shard'), ('-wal', b'old wal'), ('-shm', b'old shm')):
            (self.live/(rel+suffix)).write_bytes(value)
        (self.plan/'candidates'/rel).write_bytes(b'new shard')
        path = self.plan/'plan.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        value['originals'] = ops.state(self.live)
        value['candidates'][rel] = digest(self.plan/'candidates'/rel)
        path.write_text(json.dumps(value), encoding='utf-8')
        before = ops.state(self.live)
        real = os.replace
        def fail(source, target):
            if str(source).endswith('message_resource.db.chat-rescue-stage'):
                raise OSError('synthetic third replacement failure')
            return real(source, target)
        with patch('chat_rescue.operations.os.replace', side_effect=fail):
            with self.assertRaises(OSError):
                self.run_apply()
        self.assertEqual(ops.state(self.live), before)


class WindowsMessageTypesTests(unittest.TestCase):
    setUp = ShardTests.setUp
    save = ShardTests.save

    def test_nontext_one_second_difference_is_a_conflict(self):
        for root in (self.source, self.target):
            private_json(root/'direction-profile.json', {'direction_profile': 'windows-4.2.0.12-structured'})
        with db(self.source/'message/message_0.db') as c:
            c.execute('UPDATE ' + table_for(A) + ' SET local_type=3')
            c.execute('UPDATE ' + table_for(A) + ' SET packed_info_data=? WHERE server_id=102', (bytes.fromhex('08091003'),))
        with db(self.source/'message/message_resource.db') as c:
            c.execute('UPDATE MessageResourceInfo SET message_local_type=3')
        self.save()
        prepare_plain_restore(self.bundle, self.target, A, self.root/'first')
        with db(self.root/'first/message/message_0.db') as c:
            c.execute('UPDATE ' + table_for(B) + ' SET create_time=create_time+1,sort_seq=sort_seq+1000')
        with self.assertRaisesRegex(ValueError, 'collision'):
            prepare_plain_restore(self.bundle, self.root/'first', A, self.root/'result')

    def test_windows_all_structured_types_both_directions_and_repeat(self):
        types = [1, 3, 34, 42, 43, 47, 48, 49, (6 << 32) | 49,
                 (5 << 32) | 49, (57 << 32) | 49, (19 << 32) | 49, 50, 10000, 999]
        profile = 'windows-4.2.0.12-structured'
        for root in (self.source, self.target):
            private_json(root/'direction-profile.json', {'direction_profile': profile})
        with db(self.source/'message/message_0.db') as c:
            c.execute('DELETE FROM ' + table_for(A))
            for index, local_type in enumerate(types):
                for direction in (0, 1):
                    sid = 1000 + index * 2 + direction
                    c.execute('INSERT INTO ' + table_for(A) +
                              ' (server_id,local_type,sort_seq,real_sender_id,create_time,status,message_content,packed_info_data)'
                              ' VALUES(?,?,?,?,?,?,?,?)',
                              (sid, local_type, sid * 1000, 7 if direction == 0 else 9, sid, 3,
                               b'<synthetic opaque payload>', bytes.fromhex('080710025a027879' if direction == 0 else '080910035a027879')))
        with db(self.source/'message/message_resource.db') as c:
            c.execute('DELETE FROM MessageResourceInfo'); c.execute('DELETE FROM MessageResourceDetail')
        self.assertEqual(self.save()['saved'], 30)
        result = prepare_plain_restore(self.bundle, self.target, A, self.root/'result')
        self.assertEqual(result['added'], 30)
        with db(self.root/'result/message/message_0.db') as c:
            rows = c.execute('SELECT local_type,real_sender_id,status,hex(packed_info_data),message_content FROM ' +
                             table_for(B) + ' ORDER BY server_id').fetchall()
        for index, local_type in enumerate(types):
            self.assertEqual(rows[index * 2], (local_type, 4, 2 if local_type == 1 else 3, '080410035A027879', b'<synthetic opaque payload>'))
            self.assertEqual(rows[index * 2 + 1], (local_type, 302, 3, '08AE0210025A027879', b'<synthetic opaque payload>'))
        again = prepare_plain_restore(self.bundle, self.root/'result', A, self.root/'again')
        self.assertEqual((again['added'], again['skipped']), (0, 30))


if __name__ == '__main__':
    unittest.main()
