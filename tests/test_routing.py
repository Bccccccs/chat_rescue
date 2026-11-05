"""Native range witnesses and date routing use synthetic saved databases only."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from test_workflow import fixture,db,A,B,FIELDS
from test_participant_ids import packed
from chat_rescue.routing import routing_inventory,routed_restore
from chat_rescue.workflow import table_for,private_json,save_chat,digest,WINDOWS_PARTICIPANT_PROFILE


def ensure_fts(root):
    with db(root/'message/message_resource.db') as c:
        c.execute('CREATE TABLE IF NOT EXISTS FtsRange(session_id INTEGER,db_time_stamp INTEGER,start_local_id INTEGER,end_local_id INTEGER,range_type INTEGER)')


def add_witnesses(root,rel,stamp,when,label):
    ensure_fts(root)
    for index in range(2):
        name='synthetic_witness_'+label+'_'+str(index)
        with db(root/rel) as c:
            c.execute('CREATE TABLE '+table_for(name)+'('+FIELDS+')')
            c.execute('INSERT INTO '+table_for(name)+'(server_id,local_type,create_time) VALUES(?,1,?)',(8000+index,when))
        with db(root/'message/message_resource.db') as c:
            mapping=c.execute('INSERT INTO ChatName2Id(user_name,update_time) VALUES(?,0)',(name,)).lastrowid
            c.executemany('INSERT INTO FtsRange VALUES(?,?,1,1,?)',[(mapping,stamp,1),(mapping,stamp,2)])


class RoutingInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'target';fixture(self.root,False)
        shutil.copy2(self.root/'message/message_0.db',self.root/'message/message_2.db')

    def test_mapping_is_proved_without_assuming_file_number_chronology(self):
        add_witnesses(self.root,'message/message_2.db',1000,1100,'old')
        add_witnesses(self.root,'message/message_0.db',2000,2100,'new')
        result=routing_inventory(self.root,A,B)
        self.assertEqual([(r['path'],r['timestamp']) for r in result['shards']],
                         [('message/message_2.db',1000),('message/message_0.db',2000)])
        self.assertEqual([r['independent_witnesses'] for r in result['shards']],[2,2])

    def test_missing_or_contradictory_native_evidence_is_refused(self):
        with self.assertRaisesRegex(ValueError,'FtsRange'):routing_inventory(self.root,A,B)
        add_witnesses(self.root,'message/message_0.db',1000,2100,'old')
        add_witnesses(self.root,'message/message_2.db',2000,2100,'new')
        with self.assertRaisesRegex(ValueError,'时间范围不一致'):routing_inventory(self.root,A,B)


@unittest.skipUnless(os.name=='nt','Windows routed replacement scope')
class RoutedRestoreTests(unittest.TestCase):
    def test_old_and_new_rows_have_correct_shard_local_identity_ids_and_fts_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);target=root/'A';source=root/'B';fixture(target,False);fixture(source,True)
            shutil.copy2(target/'message/message_0.db',target/'message/message_2.db')
            add_witnesses(target,'message/message_0.db',1699999900,1700000000,'old')
            add_witnesses(target,'message/message_2.db',1700000001,1700000100,'new')
            for rel,own,peer in (('message/message_0.db',4,400),('message/message_2.db',15,68)):
                with db(target/rel) as c:
                    c.execute('UPDATE Name2Id SET rowid=? WHERE user_name=?',(own,A))
                    c.execute('INSERT INTO Name2Id(rowid,user_name,is_session) VALUES(?,?,0)',(peer,B))
            with db(source/'message/message_0.db') as c:
                c.execute('UPDATE '+table_for(A)+' SET packed_info_data=? WHERE server_id=101',(packed(7,9),))
                c.execute('UPDATE '+table_for(A)+' SET packed_info_data=? WHERE server_id=102',(packed(9,7),))
            for plain in (source,target):private_json(plain/'direction-profile.json',{'direction_profile':WINDOWS_PARTICIPANT_PROFILE})
            save_chat(source,B,A,root/'bundle')
            with db(target/'message/message_resource.db') as c:
                selected=c.execute('INSERT INTO ChatName2Id(user_name,update_time) VALUES(?,0)',(B,)).lastrowid
                c.execute('INSERT INTO FtsRange VALUES(?,1699999900,1,999,1)',(selected,))
            inventory=routing_inventory(target,A,B)
            before=digest(target/'message/message_resource.db')
            result=routed_restore(root/'bundle',target,A,root/'candidate',inventory)
            self.assertEqual(result['added'],2)
            self.assertEqual([r['imported'] for r in result['target_shard_imports']],[1,1])
            for rel,expected in (('message/message_0.db',(101,1,4,packed(4,400))),
                                 ('message/message_2.db',(102,1,68,packed(68,15)))):
                with db(root/'candidate'/rel) as c:
                    self.assertEqual(c.execute('SELECT server_id,local_id,real_sender_id,packed_info_data FROM '+table_for(B)).fetchone(),expected)
                    self.assertEqual(c.execute('SELECT is_session FROM Name2Id WHERE user_name=?',(B,)).fetchone()[0],1)
            with db(root/'candidate/message/message_resource.db') as c:
                self.assertEqual(c.execute('SELECT count(*) FROM FtsRange WHERE session_id=?',(selected,)).fetchone()[0],0)
                self.assertEqual(c.execute('SELECT count(*) FROM FtsRange').fetchone()[0],8)
            self.assertEqual(digest(target/'message/message_resource.db'),before)
            with db(target/'message/message_resource.db') as c:
                c.execute('UPDATE FtsRange SET range_type=99 WHERE session_id=?',(selected,))
            with self.assertRaisesRegex(ValueError,'未知搜索范围类型'):
                routed_restore(root/'bundle',target,A,root/'rejected/candidate',inventory)
