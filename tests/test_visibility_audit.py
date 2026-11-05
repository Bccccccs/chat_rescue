"""Client metadata inspection only uses saved synthetic snapshots."""
import json
from pathlib import Path
import tempfile
import unittest
from test_workflow import fixture,db,A,B
from chat_rescue.visibility_audit import inspect_snapshot
from chat_rescue.workflow import digest,private_json
from chat_rescue.shards import database_rels


class VisibilityAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'plain';fixture(self.root,True)
        with db(self.root/'message/message_0.db') as c:
            c.execute('CREATE TABLE db_info(Key TEXT,ValueInt64 INTEGER,ValueStdStr TEXT)')
            c.executemany('INSERT INTO db_info VALUES(?,?,?)',[
                ('timestamp',1782864000,'do not expose'),('wxid_private_timestamp',42,'private'),
                ('owner_timestamp',43,'private'),('unrelated',44,'private')])
            c.execute('CREATE TABLE wcdb_builtin_compression_record(table_name TEXT)')
            c.execute("INSERT INTO wcdb_builtin_compression_record VALUES('private table name')")
        with db(self.root/'message/message_resource.db') as c:
            c.execute('CREATE TABLE FtsRange(session_id INTEGER,db_time_stamp INTEGER,start_local_id INTEGER,end_local_id INTEGER,range_type INTEGER)')
            c.executemany('INSERT INTO FtsRange VALUES(?,?,?,?,?)',[(11,1782864000,1,2,1),(99,0,3,4,0)])
        private_json(self.root/'export-report.json',{'exported':{r:digest(self.root/r) for r in database_rels(self.root)}})

    def test_metadata_and_compression_summary_preserve_source_and_exclude_payloads(self):
        before={r:digest(self.root/r) for r in database_rels(self.root)}
        report=inspect_snapshot(self.root,A,verify_export=True)
        shard=report['message_shards']['message/message_0.db']
        self.assertEqual(shard['database_metadata']['db_info']['numeric_time_and_version_entries'],[
            {'key':'timestamp','value':1782864000}])
        self.assertEqual(shard['database_metadata']['wcdb_builtin_compression_record']['rows'],1)
        self.assertEqual(report['resource_metadata']['FtsRange']['selected_chat_rows'],1)
        self.assertEqual(sum(m['rows'] for m in shard['months'].values()),2)
        text=json.dumps(report)
        for private in (A,B,'wxid_private','private table name','Synthetic card','do not expose','"server_id":'):
            self.assertNotIn(private,text)
        self.assertEqual(before,{r:digest(self.root/r) for r in database_rels(self.root)})

    def test_modified_snapshot_is_refused(self):
        with db(self.root/'message/message_0.db') as c:c.execute("UPDATE db_info SET ValueInt64=0")
        with self.assertRaisesRegex(ValueError,'hashes changed'):inspect_snapshot(self.root,A,verify_export=True)


if __name__=='__main__':unittest.main()
