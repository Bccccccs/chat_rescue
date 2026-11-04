"""Evidence summaries must reveal neither identities nor content/real IDs."""
import json
from pathlib import Path
import tempfile
import unittest
from test_workflow import fixture, db, A, B, FIELDS
from chat_rescue.direction_diagnostics import diagnose, packed_fields
from chat_rescue.workflow import table_for, private_json, digest, WINDOWS_STRUCTURED_PROFILE
from chat_rescue.shards import database_rels


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.a=self.root/'a'; self.b=self.root/'b'
        fixture(self.a,False); fixture(self.b,True)
        with db(self.b/'message/message_0.db') as c:
            c.execute('UPDATE '+table_for(A)+' SET local_type=1,packed_info_data=? WHERE server_id=102',(bytes.fromhex('0809100f'),))
            c.execute('UPDATE '+table_for(A)+' SET message_content=?', (b'synthetic private body',))
        with db(self.a/'message/message_0.db') as c:
            c.execute('INSERT INTO Name2Id(rowid,user_name,is_session) VALUES(400,?,1)',(B,))
            c.execute('CREATE TABLE '+table_for(B)+'('+FIELDS+')')
            for sid,sender,flag in ((101,4,3),(102,400,14)):
                from chat_rescue.workflow import varint
                packed=b'\x08'+varint(sender)+b'\x10'+varint(flag)
                c.execute('INSERT INTO '+table_for(B)+'(server_id,local_type,real_sender_id,packed_info_data,message_content,status) VALUES(?,1,?,?,?,3)',(sid,sender,packed,b'synthetic private body'))
        self.report(self.a,A);self.report(self.b,B)

    def report(self,root,owner):
        private_json(root/'export-report.json',{'account_directory':owner,'direction_profile':WINDOWS_STRUCTURED_PROFILE,
                     'exported':{r:digest(root/r) for r in database_rels(root)}})

    def test_15_pair_is_counted_without_assuming_a_mask_or_leaking_data(self):
        before={str(p):digest(p) for root in (self.a,self.b) for p in root.rglob('*.db')}
        result=diagnose(self.a,self.b,A,B)
        pair=next(p for p in result['verified_pairs'] if p['direction']=='B_sent_A_received')
        self.assertEqual((pair['A_field2'],pair['B_field2'],pair['count']),(14,15,1))
        summary=json.dumps(result)
        for secret in (A,B,'synthetic private body','server_id','packed_info_data',str(self.root)):
            self.assertNotIn(secret,summary)
        self.assertEqual(before,{str(p):digest(p) for root in (self.a,self.b) for p in root.rglob('*.db')})

    def test_bad_sender_and_duplicate_fields_are_not_valid_evidence(self):
        self.assertEqual(packed_fields(bytes.fromhex('0809100f'),8),(15,False))
        self.assertEqual(packed_fields(bytes.fromhex('0809100f1003'),9),(None,False))

    def test_export_change_and_wrong_owner_are_rejected(self):
        with self.assertRaises(ValueError):diagnose(self.a,self.b,B,A)
        with db(self.b/'message/message_0.db') as c:c.execute('UPDATE '+table_for(A)+' SET status=9')
        with self.assertRaisesRegex(ValueError,'哈希'):diagnose(self.a,self.b,A,B)

    def test_different_body_is_not_used_to_infer_direction(self):
        with db(self.a/'message/message_0.db') as c:c.execute('UPDATE '+table_for(B)+' SET message_content=? WHERE server_id=102',(b'other text',))
        (self.a/'export-report.json').unlink();self.report(self.a,A)
        result=diagnose(self.a,self.b,A,B)
        self.assertFalse(any(p['direction']=='B_sent_A_received' for p in result['verified_pairs']))
        self.assertEqual(result['unmatched_content_or_sender_pairs'],1)

    def test_field2_local_sender_reference_is_checked_without_exposing_identity(self):
        from chat_rescue.workflow import varint
        for root, peer, sender_id in ((self.a,B,400),(self.b,A,9)):
            with db(root/'message/message_0.db') as c:
                c.execute('UPDATE '+table_for(peer)+' SET packed_info_data=? WHERE server_id=102',
                          (b'\x08'+varint(sender_id)+b'\x10'+varint(sender_id),))
            (root/'export-report.json').unlink()
            self.report(root,A if root==self.a else B)
        result=diagnose(self.a,self.b,A,B)
        for role, sender in (('A','peer'),('B','self')):
            group=next(g for g in result[role]['groups'] if g['field2_equals_real_sender_id'])
            self.assertEqual(group['field2_Name2Id_role'],sender)
            self.assertEqual(group['message_shard'],'message/message_0.db')
        self.assertEqual(result['version'],2)
