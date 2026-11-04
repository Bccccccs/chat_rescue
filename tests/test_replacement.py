"""Single-conversation destructive candidate scope; synthetic encrypted accounts only."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
import test_cli
from test_workflow import A, B, FIELDS, db
from chat_rescue import operations as ops, sync
from chat_rescue.replacement import make_replace_plan, MODE
from chat_rescue.cipher import encrypt_database
from chat_rescue.workflow import table_for, digest, private_json, DirectionMismatch


@unittest.skipUnless(os.name == 'nt', 'Windows conversation rebuild scope')
class ReplacementTests(unittest.TestCase):
    encrypted = test_cli.CLITests.encrypted
    tearDown = test_cli.CLITests.tearDown

    def setUp(self):
        test_cli.CLITests.setUp(self)
        self.a_plain = self.root / (A + '-plain')
        self.b_plain = self.root / (B + '-plain')
        with db(self.b_plain / ops.ALL[0]) as c:
            c.execute('UPDATE ' + table_for(A) + ' SET packed_info_data=? WHERE server_id=102',
                      (bytes.fromhex('08091003'),))
        # A's existing chat deliberately has an unsupported direction. It is
        # counted and backed up, never exported as a source conversation.
        with db(self.a_plain / ops.ALL[0]) as c:
            c.execute('INSERT INTO Name2Id(rowid,user_name,is_session) VALUES(400,?,1)', (B,))
            for peer, sid in ((B, 900), ('wxid_unrelated_demo', 901)):
                c.execute('CREATE TABLE ' + table_for(peer) + '(' + FIELDS + ')')
                c.execute('INSERT INTO ' + table_for(peer) +
                          '(server_id,local_type,real_sender_id,packed_info_data,message_content) VALUES(?,1,4,?,?)',
                          (sid, bytes.fromhex('08041000'), b'synthetic private text'))
        with db(self.a_plain / 'session/session.db') as c:
            c.execute('INSERT INTO SessionTable(username,last_timestamp,draft,summary) VALUES(?,9999999999,?,?)',
                      (B, 'A draft', 'old preview'))
        with db(self.a_plain / 'message/message_resource.db') as c:
            c.execute('INSERT INTO ChatName2Id(rowid,user_name,update_time) VALUES(1,?,0)', (B,))
            c.execute('INSERT INTO ChatName2Id(rowid,user_name,update_time) VALUES(2,?,0)', ('wxid_unrelated_demo',))
            for mid, chat, sid in ((1, 1, 900), (2, 2, 901)):
                c.execute('INSERT INTO MessageResourceInfo(message_id,chat_id,message_svr_id) VALUES(?,?,?)', (mid,chat,sid))
                c.execute('INSERT INTO MessageResourceDetail(resource_id,message_id) VALUES(?,?)', (mid,mid))
        self.install(self.target, self.a_plain)
        self.install(self.source, self.b_plain)
        for owner, (root, keys) in ((A, self.target), (B, self.source)):
            private_json(keys.with_suffix('.capture.json'), {'state':'verified', 'verified_required_databases':4,
                         'owner':owner, 'database_root':str(root), 'file_version':'4.2.0.13'})
        self.a_before, self.b_before = ops.state(self.target[0]), ops.state(self.source[0])

    def install(self, account, plain):
        root, keys = account
        for rel in ops.ALL:
            dest = Path(str(root / rel) + '.replacement-test')
            encrypt_database(plain / rel, dest, root / rel, keys)
            os.replace(dest, root / rel)

    def plan(self):
        a, ak = self.target; b, bk = self.source
        return make_replace_plan({'owner':A,'root':str(a),'keys':str(ak)},
                                 {'owner':B,'root':str(b),'keys':str(bk)}, self.root/'replace')

    def test_rebuild_preserves_other_chats_draft_ids_and_b_then_undo(self):
        with patch('chat_rescue.operations.ensure_closed'):
            result = self.plan()
            self.assertEqual(result['mode'], MODE)
            self.assertEqual(result['version'], 4)
            self.assertEqual(result['directions']['A']['removed'], 1)
            self.assertEqual(result['directions']['A']['added'], 2)
            self.assertIsNone(result['directions']['B']['plan'])
            self.assertEqual(ops.state(self.target[0]), self.a_before)
            recovered = self.root/'replace/A-plan/recovered-plain'
            with db(recovered/ops.ALL[0]) as c:
                self.assertEqual(c.execute('SELECT server_id FROM '+table_for(B)+' ORDER BY server_id').fetchall(), [(101,), (102,)])
                self.assertEqual(c.execute('SELECT server_id FROM '+table_for('wxid_unrelated_demo')).fetchall(), [(901,)])
                self.assertEqual(c.execute('SELECT rowid FROM Name2Id WHERE user_name=?',(B,)).fetchone(), (400,))
            with db(recovered/'message/message_resource.db') as c:
                self.assertEqual(c.execute('SELECT chat_id,message_svr_id FROM MessageResourceInfo WHERE chat_id=2').fetchall(), [(2,901)])
                self.assertEqual(c.execute('SELECT count(*) FROM MessageResourceDetail WHERE message_id=2').fetchone()[0], 1)
            with db(recovered/'session/session.db') as c:
                self.assertEqual(c.execute('SELECT draft,is_hidden,last_msg_sender FROM SessionTable WHERE username=?',(B,)).fetchone(), ('A draft',0,B))
            self.assertEqual(sync.apply_sync_plan(self.root/'replace')['state'], 'applied')
            self.assertEqual(ops.state(self.source[0]), self.b_before)
            self.assertEqual(sync.undo_sync_plan(self.root/'replace')['state'], 'undone')
            self.assertEqual(ops.state(self.target[0]), self.a_before)
            self.assertEqual(ops.state(self.source[0]), self.b_before)

    def test_neutral_types_skipped_but_unknown_ordinary_direction_refused(self):
        with db(self.b_plain/ops.ALL[0]) as c:
            c.execute('INSERT INTO '+table_for(A)+'(server_id,local_type,sort_seq,real_sender_id,packed_info_data) VALUES(0,10000,1,999,?)', (b'unknown',))
        self.install(self.source, self.b_plain)
        with patch('chat_rescue.operations.ensure_closed'):
            result = self.plan()
        self.assertEqual(result['directions']['A']['excluded_system_messages'], {'10000':1})
        self.assertEqual(result['directions']['A']['added'], 2)
        with db(self.b_plain/ops.ALL[0]) as c:
            c.execute('UPDATE '+table_for(A)+' SET packed_info_data=? WHERE server_id=102', (bytes.fromhex('08091004'),))
        self.install(self.source, self.b_plain)
        other = self.root/'different'; other.mkdir()
        a, ak = self.target; b, bk = self.source
        with patch('chat_rescue.operations.ensure_closed'), self.assertRaises(DirectionMismatch) as caught:
            make_replace_plan({'owner':A,'root':str(a),'keys':str(ak)}, {'owner':B,'root':str(b),'keys':str(bk)}, other/'plan')
        self.assertEqual((caught.exception.actual,caught.exception.expected,caught.exception.local_type), (4,3,42))
        self.assertFalse((other/'plan/sync-plan.json').exists())
        self.assertEqual(ops.state(self.target[0]), self.a_before)

    def test_changed_source_blocks_apply_before_any_write(self):
        with patch('chat_rescue.operations.ensure_closed'):
            self.plan()
            (self.source[0]/'message/message_0.db-wal').write_bytes(b'synthetic changed sidecar')
            with self.assertRaisesRegex(ValueError, '已改变'):
                sync.apply_sync_plan(self.root/'replace')
        self.assertEqual(ops.state(self.target[0]), self.a_before)

    def test_other_conversation_server_id_collision_is_not_erased(self):
        with db(self.a_plain/ops.ALL[0]) as c:
            c.execute('UPDATE '+table_for('wxid_unrelated_demo')+' SET server_id=101')
        self.install(self.target,self.a_plain)
        before=ops.state(self.target[0])
        with patch('chat_rescue.operations.ensure_closed'), self.assertRaisesRegex(ValueError,'another conversation'):
            self.plan()
        self.assertEqual(ops.state(self.target[0]), before)

    def test_selected_chat_removed_from_all_target_shards(self):
        root, keys = self.target
        (root/'message/message_2.db').write_bytes((root/'message/message_0.db').read_bytes())
        payload=json.loads(keys.read_text(encoding='utf-8'))
        payload['keys']['message/message_2.db']=payload['keys']['message/message_0.db']
        keys.write_text(json.dumps(payload),encoding='utf-8')
        receipt=keys.with_suffix('.capture.json')
        value=json.loads(receipt.read_text(encoding='utf-8'))
        value.update(verified_required_databases=5,verified_databases=[*ops.ALL,'message/message_2.db'])
        receipt.write_text(json.dumps(value),encoding='utf-8')
        before=ops.state(root)
        with patch('chat_rescue.operations.ensure_closed'):
            result=self.plan()
        self.assertEqual(result['directions']['A']['removed'],2)
        self.assertEqual(result['directions']['A']['added'],2)
        self.assertEqual(ops.state(root),before)
        recovered=self.root/'replace/A-plan/recovered-plain'
        messages=[]
        for rel in ('message/message_0.db','message/message_2.db'):
            with db(recovered/rel) as c:
                if c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table_for(B),)).fetchone():
                    messages.extend(c.execute('SELECT server_id FROM '+table_for(B)).fetchall())
                self.assertEqual(c.execute('SELECT server_id FROM '+table_for('wxid_unrelated_demo')).fetchall(),[(901,)])
        self.assertEqual(sorted(messages),[(101,),(102,)])

    def test_replacement_mode_rejects_any_b_write_plan(self):
        with patch('chat_rescue.operations.ensure_closed'):
            self.plan()
            path=self.root/'replace/sync-plan.json'
            value=json.loads(path.read_text(encoding='utf-8'))
            value['directions']['B']['plan']=str(self.root/'replace/A-plan')
            path.write_text(json.dumps(value),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'B 必须保持只读'):
                sync.apply_sync_plan(self.root/'replace')
        self.assertEqual(ops.state(self.target[0]),self.a_before)
        self.assertEqual(ops.state(self.source[0]),self.b_before)

    def test_missing_receipt_refuses_replacement(self):
        self.source[1].with_suffix('.capture.json').unlink()
        with patch('chat_rescue.operations.ensure_closed'),self.assertRaisesRegex(ValueError,'取钥回执'):
            self.plan()
        self.assertEqual(ops.state(self.target[0]),self.a_before)
