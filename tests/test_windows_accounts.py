"""Account hints use indexed synthetic metadata, never keys or chat contents."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from chat_rescue import windows_backend as backend
from chat_rescue.workflow import private_json
from chat_rescue.runtime import make_private
from chat_rescue.operations import ALL


class AccountDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve(); self.work=self.base/'work'; self.work.mkdir(); make_private(self.work)
        self.roots=[]
        for name,value in [('workspace',lambda:self.work),('saved_settings',lambda:{})]:
            p=patch.object(backend,name,value);p.start();self.addCleanup(p.stop)
        p=patch.object(backend.cli,'roots',side_effect=lambda:{'database_roots':list(self.roots)});p.start();self.addCleanup(p.stop)
        p=patch.dict('os.environ',{'APPDATA':str(self.base/'no-config')});p.start();self.addCleanup(p.stop)

    def root(self,owner='synthetic_one',complete=True):
        root=self.base/'xwechat_files'/(owner+'_abcd')/'db_storage';root.mkdir(parents=True)
        if complete:
            for rel in ALL:
                path=root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
        self.roots.append(str(root)); return root

    def registry(self,root,owner,nickname):
        private_json(self.work/'account-material.json',{'synthetic-index':{'root':str(root),'owner':owner,
                     'nickname':nickname,'keys':'must-not-be-opened.json'}})

    def test_registry_nickname_survives_role_changes_and_does_not_read_keys(self):
        root=self.root();self.registry(root,'synthetic_one','合成昵称')
        with patch.object(backend,'load_key',side_effect=AssertionError('no key reads')):
            item=backend.discover()[0]
        self.assertEqual(item['label'],'合成昵称 · 上次识别')
        self.assertEqual(item['nickname'],'合成昵称');self.assertEqual(item['state'],'historical')
        self.assertNotIn('keys',item);self.assertFalse(item['verified'])

    def test_two_roots_same_nickname_are_distinct_and_no_process_query(self):
        first=self.root('synthetic_one');second=self.root('synthetic_two')
        private_json(self.work/'account-material.json',{str(i):{'root':str(root),'owner':owner,'nickname':'同名'}
                     for i,(root,owner) in enumerate(((first,'synthetic_one'),(second,'synthetic_two')))})
        with patch.object(backend,'main_processes',side_effect=AssertionError('discovery is not process matching')):
            values=backend.discover()
        self.assertEqual(len(values),2);self.assertNotEqual(values[0]['label'],values[1]['label'])
        self.assertTrue(all(v['nickname']=='同名' for v in values))

    def test_explicit_task_index_and_settings_hints_but_no_unindexed_jobs(self):
        root=self.root(); task=self.work/'tasks'/('a'*32);task.mkdir(parents=True);make_private(task)
        private_json(task/'task.json',{'version':1,'id':'a'*32,'accounts':[{'root':str(root),'owner':'synthetic_one','nickname':'任务昵称'},{}]})
        unused=self.work/'jobs'/'unindexed';unused.mkdir(parents=True)
        private_json(unused/'verified-accounts.json',{'accounts':[{'root':str(root),'nickname':'不要读'}]})
        self.assertEqual(backend.discover()[0]['nickname'],'任务昵称')

    def test_stale_path_remains_a_disabled_hint_and_missing_files_are_explicit(self):
        root=self.base/'moved'/'synthetic_one_abcd'/'db_storage';self.registry(root,'synthetic_one','旧位置昵称')
        values=backend.discover();self.assertEqual(len(values),1)
        self.assertEqual(values[0]['state'],'missing_directory');self.assertFalse(values[0]['complete'])
        self.assertIn('路径已失效',values[0]['label'])
        missing=self.root('synthetic_two',False)
        item=next(v for v in backend.discover() if Path(v['root'])==missing.resolve())
        self.assertEqual(item['state'],'missing_databases');self.assertEqual(set(item['missing_databases']),set(ALL))

    def test_registry_wins_over_older_role_hint_but_mismatched_owner_does_not_label(self):
        root=self.root();self.registry(root,'synthetic_one','当前索引昵称')
        with patch.object(backend,'saved_settings',return_value={'accounts':[{'root':str(root),'owner':'synthetic_one','nickname':'旧角色昵称'}]}):
            self.assertEqual(backend.discover()[0]['nickname'],'当前索引昵称')
        path=self.work/'account-material.json';value=json.loads(path.read_text());value['synthetic-index']['owner']='unrelated_owner'
        path.write_text(json.dumps(value))
        self.assertEqual(backend.discover()[0]['nickname'],'')

    def test_message_and_account_parent_normalize_without_hiding_missing_databases(self):
        root=self.root()
        value=backend.dispatch({'action':'inspect-account-directory','root':str(root/'message')})
        self.assertEqual(Path(value['root']),root.resolve());self.assertIn('message',value['normalization'])
        (root/'contact/contact.db').unlink()
        value=backend.dispatch({'action':'inspect-account-directory','root':str(root.parent)})
        self.assertEqual(Path(value['root']),root.resolve());self.assertFalse(value['complete'])
        self.assertEqual(value['missing_databases'],['contact/contact.db'])
        with self.assertRaisesRegex(ValueError,'contact/contact.db'):
            backend.account({'root':str(root/'message'),'owner':'synthetic_one'})
        with self.assertRaisesRegex(ValueError,'db_storage'):
            backend.dispatch({'action':'inspect-account-directory','root':str(root.parent.parent)})

    def test_multiple_conflicting_task_nicknames_are_hints_not_an_arbitrary_winner(self):
        root=self.root()
        for n,nickname in (('a','合成甲'),('b','合成乙')):
            task=self.work/'tasks'/(n*32);task.mkdir(parents=True);make_private(task)
            private_json(task/'task.json',{'version':1,'id':n*32,'accounts':[{'root':str(root),'owner':'synthetic_one','nickname':nickname},{}]})
        item=backend.discover()[0]
        self.assertEqual(item['nickname'],'');self.assertEqual(set(item['nickname_hints']),{'合成甲','合成乙'})
        self.assertIn('不一致',item['label'])

    def test_record_read_hints_are_not_lost_when_the_role_changes(self):
        root=self.root(); selected={'root':str(root),'owner':'synthetic_one','keys':'unopened-private-key-path','nickname':'角色独立昵称'}
        backend.record_read({'slot':'B'},selected)
        with patch.object(backend,'saved_settings',return_value={'accounts':[{},{}]}):
            first=backend.discover()[0]
        backend.record_read({'slot':'A'},selected)
        self.assertEqual(backend.discover()[0]['nickname'],first['nickname'])
        self.assertEqual(first['nickname'],'角色独立昵称')

    def test_discovery_leaves_indexes_and_saved_settings_unchanged(self):
        root=self.root();self.registry(root,'synthetic_one','只读提示')
        settings=self.work/'settings.json';private_json(settings,{'accounts':[{'root':str(root)},{}]})
        before={path:path.read_bytes() for path in (settings,self.work/'account-material.json')}
        backend.discover();backend.discover()
        self.assertTrue(all(path.read_bytes()==value for path,value in before.items()))

    def test_malformed_or_foreign_task_index_does_not_break_discovery(self):
        root=self.root()
        directory=self.work/'tasks'/('c'*32);directory.mkdir(parents=True);make_private(directory)
        private_json(directory/'task.json',{'version':1,'id':'wrong-index','accounts':[{'root':str(root),'nickname':'不采用'}]})
        (self.work/'account-material.json').write_text('invalid-json')
        make_private(self.work/'account-material.json')
        self.assertEqual(backend.discover()[0]['nickname'],'')
