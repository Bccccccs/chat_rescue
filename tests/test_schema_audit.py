"""Schema failure evidence contains structure only, not saved conversation data."""
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from test_workflow import fixture,db,A,B
from chat_rescue.schema_audit import inspect_pair,safe_sql,verify_snapshot
from chat_rescue.workflow import digest,private_json
from chat_rescue.shards import database_rels


class SchemaAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.a=self.root/'A';self.b=self.root/'B'
        fixture(self.a,False);fixture(self.b,True)
        shutil.copy2(self.b/'message/message_0.db',self.b/'message/message_1.db')
        with db(self.b/'message/message_1.db') as c:
            c.execute("CREATE TABLE ExtraAux(field INTEGER,note TEXT DEFAULT 'synthetic private literal')")
        for root in (self.a,self.b):
            private_json(root/'export-report.json',{'exported':{rel:digest(root/rel) for rel in database_rels(root)}})

    def test_exact_auxiliary_difference_is_identified_without_account_or_payload(self):
        before={root:{rel:digest(root/rel) for rel in database_rels(root)} for root in (self.a,self.b)}
        report=inspect_pair(self.a,self.b,A,B)
        changed=report['message_shards'][1]
        self.assertFalse(changed['auxiliary_guard_matches'])
        self.assertEqual(changed['auxiliary_differences'][0]['table'],'ExtraAux')
        self.assertTrue(changed['selected_chat_structure_matches'])
        text=json.dumps(report)
        for value in (A,B,'synthetic private literal','Synthetic card','message_content BLOB'):
            self.assertNotIn(value,text)
        self.assertIn('<literal>',text)
        for root,hashes in before.items():self.assertEqual(hashes,{rel:digest(root/rel) for rel in database_rels(root)})

    def test_modified_export_is_refused(self):
        with db(self.b/'message/message_1.db') as c:c.execute('ALTER TABLE ExtraAux ADD COLUMN changed TEXT')
        with self.assertRaisesRegex(ValueError,'hash'):inspect_pair(self.a,self.b,A,B)

    def test_literals_and_dynamic_names_are_redacted(self):
        text=safe_sql("DEFAULT 'private''text' Msg_"+'a'*32+' wxid_private_account')
        self.assertNotIn('private',text);self.assertNotIn('a'*32,text)

    def test_voice_export_inventory_is_verified_and_unknown_paths_refused(self):
        with db(self.a/'message/media_0.db') as c:c.execute('CREATE TABLE VoiceInfo(synthetic INTEGER)')
        report=self.a/'export-report.json';value=json.loads(report.read_text(encoding='utf-8'))
        value['exported']['message/media_0.db']=digest(self.a/'message/media_0.db')
        report.write_text(json.dumps(value),encoding='utf-8')
        self.assertIn('message/media_0.db',verify_snapshot(self.a))
        value['exported']['../unexpected.db']='not-a-real-hash'
        report.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'inventory'):verify_snapshot(self.a)
