"""Replacement schema compatibility rejects drift without changing account data."""
from pathlib import Path
import shutil
import tempfile
import unittest
from test_workflow import fixture,db,A,B
from chat_rescue.schema_checks import replacement_schema_check
from chat_rescue.workflow import save_chat,prepare_plain_restore,table_for,digest
from chat_rescue.shards import database_rels


class SchemaCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'B';self.target=self.root/'A'
        fixture(self.source,True);fixture(self.target,False)

    def candidate(self):
        save_chat(self.source,B,A,self.root/'bundle')
        result=self.root/'candidate';prepare_plain_restore(self.root/'bundle',self.target,A,result)
        return result

    def test_candidate_structure_matches_with_independent_local_identity_values(self):
        self.assertEqual(replacement_schema_check(self.source,self.target,A,B)['state'],'matched')
        candidate=self.candidate()
        check=replacement_schema_check(self.source,candidate,A,B,candidate=True)
        self.assertEqual(check['state'],'matched')
        self.assertEqual(check['selected_candidate_tables_checked'],1)
        self.assertFalse(check['client_visibility_verified'])

    def test_auxiliary_schema_drift_refused_without_changing_target(self):
        with db(self.target/'message/message_resource.db') as c:
            c.execute('ALTER TABLE MessageResourceInfo ADD COLUMN incompatible INTEGER')
        before={r:digest(self.target/r) for r in database_rels(self.target)}
        with self.assertRaisesRegex(ValueError,'辅助库'):
            replacement_schema_check(self.source,self.target,A,B)
        self.assertEqual(before,{r:digest(self.target/r) for r in database_rels(self.target)})

    def test_missing_or_changed_candidate_index_is_refused(self):
        candidate=self.candidate()
        with db(candidate/'message/message_0.db') as c:
            c.execute('DROP INDEX '+table_for(B)+'_SORTSEQ')
            c.execute('CREATE INDEX '+table_for(B)+'_SORTSEQ ON '+table_for(B)+'(sort_seq DESC)')
        with self.assertRaisesRegex(ValueError,'会话字段或索引'):
            replacement_schema_check(self.source,candidate,A,B,candidate=True)

    def test_source_shard_schema_drift_and_unhandled_triggers_are_refused(self):
        other=self.source/'message/message_2.db'
        shutil.copy2(self.source/'message/message_0.db',other)
        with db(other) as c:c.execute('ALTER TABLE '+table_for(A)+' ADD COLUMN unknown_field INTEGER')
        with self.assertRaisesRegex(ValueError,'B 各分片的会话'):
            replacement_schema_check(self.source,self.target,A,B)
        other.unlink()
        with db(self.source/'message/message_0.db') as c:
            c.execute('CREATE TRIGGER unknown_trigger AFTER INSERT ON '+table_for(A)+' BEGIN SELECT 1; END')
        with self.assertRaisesRegex(ValueError,'触发器'):
            replacement_schema_check(self.source,self.target,A,B)

    def test_matching_unknown_auxiliary_triggers_are_still_refused(self):
        for root in (self.source,self.target):
            with db(root/'message/message_0.db') as c:
                c.execute('CREATE TRIGGER unknown_aux AFTER UPDATE ON Name2Id BEGIN SELECT 1; END')
        with self.assertRaisesRegex(ValueError,'辅助表结构'):
            replacement_schema_check(self.source,self.target,A,B)
