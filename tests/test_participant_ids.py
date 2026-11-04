"""Sparse shard-local sender/recipient references, never fixed direction numbers."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from test_workflow import fixture, db, A, B
from chat_rescue.workflow import (WINDOWS_PARTICIPANT_PROFILE as PROFILE, WINDOWS_STRUCTURED_PROFILE,
    save_chat, prepare_plain_restore, private_json, table_for, varint, digest, unpack, DirectionMismatch)


def packed(sender,recipient):return b'\x08'+varint(sender)+b'\x10'+varint(recipient)+b'\x58\x00'


class ParticipantTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'source';self.target=self.root/'target'
        fixture(self.source,True);fixture(self.target,False)
        for root in (self.source,self.target):private_json(root/'direction-profile.json',{'direction_profile':PROFILE})
        with db(self.source/'message/message_0.db') as c:
            c.execute('UPDATE '+table_for(A)+' SET packed_info_data=? WHERE server_id=101',(packed(7,9),))
            c.execute('UPDATE '+table_for(A)+' SET packed_info_data=? WHERE server_id=102',(packed(9,7),))

    def restore(self,name='restored'):
        bundle=self.root/'bundle';save_chat(self.source,B,A,bundle)
        return prepare_plain_restore(bundle,self.target,A,self.root/name)

    def test_both_id_fields_remapped_and_repeat_deduplicates(self):
        result=self.restore()
        self.assertEqual(result['added'],2)
        with db(self.root/'restored/message/message_0.db') as c:
            rows=c.execute('SELECT server_id,real_sender_id,hex(packed_info_data),status FROM '+table_for(B)+' ORDER BY server_id').fetchall()
        self.assertEqual(rows,[(101,4,packed(4,302).hex().upper(),4),(102,302,packed(302,4).hex().upper(),2)])
        result=prepare_plain_restore(self.root/'bundle',self.root/'restored',A,self.root/'repeat')
        self.assertEqual((result['added'],result['skipped']),(0,2))

    def test_text_status_uses_target_perspective(self):
        with db(self.source/'message/message_0.db') as c:c.execute('UPDATE '+table_for(A)+' SET local_type=1')
        with db(self.source/'message/message_resource.db') as c:
            c.execute('DELETE FROM MessageResourceDetail');c.execute('DELETE FROM MessageResourceInfo')
        self.restore()
        with db(self.root/'restored/message/message_0.db') as c:
            self.assertEqual(c.execute('SELECT status FROM '+table_for(B)+' ORDER BY server_id').fetchall(),[(2,),(3,)])

    def test_multishard_15_sender_and_recipient_values_deduplicate_by_identity(self):
        extra=self.source/'message/message_2.db';shutil.copy2(self.source/'message/message_0.db',extra)
        with db(extra) as c:
            c.execute('UPDATE Name2Id SET rowid=68 WHERE user_name=?',(A,))
            c.execute('UPDATE Name2Id SET rowid=15 WHERE user_name=?',(B,))
            c.execute('UPDATE '+table_for(A)+' SET real_sender_id=68,packed_info_data=? WHERE server_id=101',(packed(68,15),))
            c.execute('UPDATE '+table_for(A)+' SET real_sender_id=15,packed_info_data=? WHERE server_id=102',(packed(15,68),))
        result=self.restore()
        self.assertEqual(result['added'],2)
        data=json.loads((self.root/'bundle/conversation.json').read_text(encoding='utf-8'))
        self.assertEqual(data['participant_ids']['message/message_2.db'],{A:68,B:15})

    def test_wrong_recipient_and_forged_bundle_mapping_are_refused(self):
        with db(self.source/'message/message_0.db') as c:
            c.execute('UPDATE '+table_for(A)+' SET packed_info_data=? WHERE server_id=102',(packed(9,15),))
        with self.assertRaises(DirectionMismatch):save_chat(self.source,B,A,self.root/'bad')
        with db(self.source/'message/message_0.db') as c:
            c.execute('UPDATE '+table_for(A)+' SET packed_info_data=? WHERE server_id=102',(packed(9,7),))
        bundle=self.root/'bundle';save_chat(self.source,B,A,bundle)
        path=bundle/'conversation.json';data=json.loads(path.read_text(encoding='utf-8'))
        data['participant_ids']['message/message_0.db']={A:9,B:7}
        path.write_text(json.dumps(data),encoding='utf-8')
        manifest=bundle/'manifest.json';value=json.loads(manifest.read_text(encoding='utf-8'));value['sha256']=digest(path)
        manifest.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'shard identity mapping'):
            prepare_plain_restore(bundle,self.target,A,self.root/'bad-result')

    def test_explicit_rebuild_mode_reuses_old_verified_profile_without_editing_export(self):
        path=self.source/'direction-profile.json';path.write_text(json.dumps({'direction_profile':WINDOWS_STRUCTURED_PROFILE}))
        before={p.name:digest(p) for p in self.source.rglob('*.db')}
        bundle=self.root/'bundle';save_chat(self.source,B,A,bundle,participant_identity_layout=True,exclude_system_messages=True)
        self.assertEqual(json.loads((bundle/'conversation.json').read_text(encoding='utf-8'))['direction_profile'],PROFILE)
        self.assertEqual(before,{p.name:digest(p) for p in self.source.rglob('*.db')})
