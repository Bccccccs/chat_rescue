"""Bidirectional integration uses synthetic encrypted accounts only."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch
import test_cli as cli_tests
from test_workflow import A, B, db
from chat_rescue.cipher import encrypt_database, export_database
from chat_rescue.operations import ALL, export_root
from chat_rescue.workflow import save_chat, prepare_plain_restore, digest, table_for
from chat_rescue import sync

class SyncTests(unittest.TestCase):
    setUp = cli_tests.CLITests.setUp
    tearDown = cli_tests.CLITests.tearDown
    encrypted = cli_tests.CLITests.encrypted

    def plan(self):
        a, ak = self.target; b, bk = self.source
        return sync.make_sync_plan({'owner': A, 'root': str(a), 'keys': str(ak)},
                                   {'owner': B, 'root': str(b), 'keys': str(bk)}, self.root/'sync')

    def test_missing_conversation_receives_without_deleting_source(self):
        with patch('chat_rescue.operations.ensure_closed'):
            result = self.plan()
            self.assertEqual(result['directions']['A']['added'], 2)
            self.assertEqual(result['directions']['B']['added'], 0)
            before = {r: digest(self.source[0]/r) for r in ALL}
            self.assertEqual(sync.apply_sync_plan(self.root/'sync')['state'], 'applied')
            self.assertEqual(before, {r: digest(self.source[0]/r) for r in ALL})
        out = self.root/'read.db'
        export_database(self.target[0]/ALL[0], out, self.target[1])
        with db(out) as c:
            self.assertEqual(c.execute('SELECT server_id FROM '+table_for(B)+' ORDER BY server_id').fetchall(), [(101,), (102,)])

    def test_both_sides_union_preserves_direction_and_deduplicates(self):
        a, ak = self.target; b, bk = self.source
        with patch('chat_rescue.operations.ensure_closed'):
            export_root(b, bk, self.root/'bp')
            save_chat(self.root/'bp', B, A, self.root/'bundle')
            export_root(a, ak, self.root/'ap')
            prepare_plain_restore(self.root/'bundle', self.root/'ap', A, self.root/'union')
        with db(self.root/'union'/ALL[0]) as c:
            c.execute('INSERT INTO '+table_for(B)+' (server_id,local_type,sort_seq,real_sender_id,create_time,status,packed_info_data,message_content) VALUES(103,1,1700000002000,4,1700000002,3,?,?)', (bytes.fromhex('08041001'), 'synthetic extra'))
        for rel in ALL:
            candidate = self.root/('new-'+Path(rel).name)
            encrypt_database(self.root/'union'/rel, candidate, a/rel, ak)
            os.replace(candidate, a/rel)
        with patch('chat_rescue.operations.ensure_closed'):
            result = self.plan()
            self.assertEqual((result['directions']['A']['added'], result['directions']['A']['skipped']), (0, 2))
            self.assertEqual((result['directions']['B']['added'], result['directions']['B']['skipped']), (1, 2))
            sync.apply_sync_plan(self.root/'sync')
        export_database(b/ALL[0], self.root/'read.db', bk)
        with db(self.root/'read.db') as c:
            self.assertEqual(c.execute('SELECT real_sender_id,hex(packed_info_data) FROM '+table_for(A)+' WHERE server_id=103').fetchone(), (7, '08071002'))
            self.assertEqual(c.execute('SELECT count(*) FROM '+table_for(A)).fetchone()[0], 3)

    def test_changed_either_account_blocks_all_writes(self):
        with patch('chat_rescue.operations.ensure_closed'):
            self.plan()
            before = digest(self.target[0]/ALL[0])
            (self.source[0]/ALL[0]).write_bytes(b'changed')
            with self.assertRaises(ValueError): sync.apply_sync_plan(self.root/'sync')
            self.assertEqual(digest(self.target[0]/ALL[0]), before)

    def test_same_account_rejected(self):
        a, ak = self.target
        with patch('chat_rescue.operations.ensure_closed'):
            with self.assertRaises(ValueError):
                sync.make_sync_plan({'owner':A,'root':str(a),'keys':str(ak)}, {'owner':A,'root':str(a),'keys':str(ak)}, self.root/'bad')

class UndoTests(unittest.TestCase):
    setUp = SyncTests.setUp
    tearDown = SyncTests.tearDown
    encrypted = SyncTests.encrypted
    plan = SyncTests.plan
    def test_undo_restores_both_original_snapshots(self):
        with patch('chat_rescue.operations.ensure_closed'):
            before={slot:sync.ops.state(pair[0]) for slot,pair in [('A',self.target),('B',self.source)]}
            self.plan();sync.apply_sync_plan(self.root/'sync')
            self.assertEqual(sync.undo_sync_plan(self.root/'sync')['state'],'undone')
            self.assertEqual(sync.ops.state(self.target[0]),before['A'])
            self.assertEqual(sync.ops.state(self.source[0]),before['B'])

    def test_undo_refuses_new_messages_or_sidecars(self):
        with patch('chat_rescue.operations.ensure_closed'):
            self.plan();sync.apply_sync_plan(self.root/'sync')
            before=digest(self.target[0]/ALL[0])
            Path(str(self.source[0]/ALL[0])+'-wal').write_bytes(b'new session data')
            with self.assertRaises(ValueError):sync.undo_sync_plan(self.root/'sync')
            self.assertEqual(digest(self.target[0]/ALL[0]),before)

class PreflightTests(unittest.TestCase):
    setUp = SyncTests.setUp
    tearDown = SyncTests.tearDown
    encrypted = SyncTests.encrypted
    plan = SyncTests.plan

    def test_candidate_tampering_blocks_all_writes(self):
        with patch('chat_rescue.operations.ensure_closed'):
            self.plan()
            candidate=self.root/'sync/A-plan/candidates'/ALL[0]
            candidate.write_bytes(b'tampered')
            before={slot:sync.ops.state(pair[0]) for slot,pair in [('A',self.target),('B',self.source)]}
            with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')
            self.assertEqual(sync.ops.state(self.target[0]),before['A'])
            self.assertEqual(sync.ops.state(self.source[0]),before['B'])

    def test_repeated_application_refused(self):
        with patch('chat_rescue.operations.ensure_closed'):
            self.plan();sync.apply_sync_plan(self.root/'sync')
            with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')

    def test_bilateral_failure_reports_first_side_not_full_success(self):
        # Complete the first real write, then fail before recording the unchanged side.
        real=sync.ops.state;failed=False
        def fail_once(root):
            nonlocal failed
            if Path(root).resolve()==self.source[0].resolve() and (self.root/'sync/A-plan/applied.json').exists() and not failed:
                failed=True;raise OSError('synthetic filesystem failure')
            return real(root)
        import json
        with patch('chat_rescue.operations.ensure_closed'):
            self.plan()
            with patch('chat_rescue.sync.ops.state',side_effect=fail_once):
                with self.assertRaises(RuntimeError):sync.apply_sync_plan(self.root/'sync')
        receipt=json.loads((self.root/'sync/sync-result.json').read_text())
        self.assertEqual(receipt['state'],'partial')
        self.assertEqual(receipt['accounts']['A']['state'],'applied')
        self.assertTrue((self.root/'sync/A-plan/originals'/ALL[0]).exists())

class LinkSafetyTests(unittest.TestCase):
    setUp = SyncTests.setUp
    tearDown = SyncTests.tearDown
    encrypted = SyncTests.encrypted
    plan = SyncTests.plan

    def test_symlink_candidate_rejected_before_coordinated_start(self):
        with patch('chat_rescue.operations.ensure_closed'):
            self.plan()
            candidate=self.root/'sync/A-plan/candidates'/ALL[0]
            elsewhere=self.root/'elsewhere';candidate.rename(elsewhere);candidate.symlink_to(elsewhere)
            with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')
            self.assertFalse((self.root/'sync/sync-started.json').exists())

class UndoPreparationTests(unittest.TestCase):
    setUp = SyncTests.setUp
    tearDown = SyncTests.tearDown
    encrypted = SyncTests.encrypted
    plan = SyncTests.plan

    def test_temporary_stage_failure_keeps_live_state_and_allows_retry(self):
        original_open=Path.open;failed=False
        def fail_once(path,*args,**kwargs):
            nonlocal failed
            if str(path).endswith('message_resource.db.chat-rescue-undo-stage') and not failed:
                failed=True;raise OSError('synthetic temporary failure')
            return original_open(path,*args,**kwargs)
        with patch('chat_rescue.operations.ensure_closed'):
            self.plan();sync.apply_sync_plan(self.root/'sync')
            before={slot:sync.ops.state(pair[0]) for slot,pair in [('A',self.target),('B',self.source)]}
            with patch.object(Path,'open',new=fail_once):
                with self.assertRaises(OSError):sync.undo_sync_plan(self.root/'sync')
            self.assertEqual(sync.ops.state(self.target[0]),before['A'])
            self.assertEqual(sync.ops.state(self.source[0]),before['B'])
            self.assertEqual(list(self.target[0].rglob('*.chat-rescue-undo-stage')),[])
            self.assertEqual(sync.undo_sync_plan(self.root/'sync')['state'],'undone')
