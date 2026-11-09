"""Task bridge state and migrated history guards, synthetic data only."""
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from chat_rescue import windows_backend as backend
from chat_rescue import portable_task as tasks
from chat_rescue.workflow import private_json, digest
from chat_rescue.runtime import make_private


class TaskBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.work=Path(self.tmp.name).resolve()/'work'; self.work.mkdir(); make_private(self.work)
        self.env=patch.object(backend,'workspace',return_value=self.work); self.env.start(); self.addCleanup(self.env.stop)

    def test_new_load_and_invalid_task(self):
        task=backend.dispatch({'action':'task-new'})
        self.assertEqual(backend.dispatch({'action':'task-load','task_id':task['id']})['id'],task['id'])
        with self.assertRaises(ValueError): backend.dispatch({'action':'task-load','task_id':'../outside'})

    def test_imported_history_cannot_apply_even_with_ready_state(self):
        parent=self.work/'history'
        parent.mkdir(); private_json(parent/'migration-history.json',{'version':1})
        plan=parent/'plan'; plan.mkdir()
        private_json(plan/'sync-plan.json',{'version':4,'mode':'replace-a-from-b','accounts':{'A':{},'B':{}},
                   'directions':{'A':{'target_shard_imports':[{}]}}})
        view=backend.dispatch({'action':'resume','plan':str(plan)})
        self.assertTrue(view['history_only'])
        with patch.object(backend,'apply_sync_plan') as apply:
            with self.assertRaises(ValueError): backend.dispatch({'action':'apply','plan':str(plan),
                        'plan_hash':digest(plan/'sync-plan.json'),'confirmed':True})
            apply.assert_not_called()

    def test_task_save_indexes_only_paths_and_resets_verification(self):
        task=backend.dispatch({'action':'task-new'})
        value=backend.dispatch({'action':'task-save','task_id':task['id'],'accounts':[{'root':'old','owner':'synthetic'},{}],
                                'errors':['','read failed'],'include_voice':True})
        self.assertTrue(value['include_voice']); self.assertEqual(value['errors'][1],'read failed')
        self.assertNotIn('verified',json.dumps(value))

    def test_loading_a_managed_history_plan_gets_its_own_task_index(self):
        old=backend.dispatch({'action':'task-new'})
        plan=self.work/'jobs'/'second'/'sync'; plan.mkdir(parents=True)
        private_json(plan/'sync-plan.json',{'version':4,'mode':'replace-a-from-b','accounts':{'A':{},'B':{}},'directions':{}})
        result=backend.dispatch({'action':'task-from-plan','plan':str(plan)})
        self.assertNotEqual(result['task']['id'],old['id'])
        self.assertEqual(result['task']['plan'],str(plan.resolve()))
        self.assertEqual(tasks.load(self.work,old['id'])['plan'],'')

    def test_capture_failure_report_is_indexed_and_exported_without_valid_keys(self):
        task=backend.dispatch({'action':'task-new'})
        def fail_capture(pid,exe,root,owner,output,**kwargs):
            private_json(Path(output).with_suffix('.capture-failed.json'),{'verified_count':0,'keys_saved':False})
            raise RuntimeError('synthetic HMAC failed')
        with patch.object(backend,'account',return_value={'root':'synthetic','owner':'synthetic','keys':''}), \
                patch.object(backend,'main_processes',return_value=[10]),patch.object(backend,'capture',side_effect=fail_capture):
            with self.assertRaisesRegex(RuntimeError,'HMAC'):
                backend.dispatch({'action':'capture','slot':'A','task_id':task['id'],'account':{},'executable':'synthetic.exe','identity_confirmed':True})
        saved=tasks.load(self.work,task['id']); self.assertTrue(Path(saved['failure_reports'][0]).is_file())
        output=Path(self.tmp.name).resolve()/'export'; tasks.export_task(self.work,task['id'],output)
        self.assertTrue((output/'artifacts/failures/0/keys.capture-failed.json').is_file())

    def test_fresh_selection_reuses_only_exact_account_cache_after_reverification(self):
        task=backend.dispatch({'action':'task-new'})
        keys=self.work/'jobs'/'known'/'keys.json'; keys.parent.mkdir(parents=True); private_json(keys,{'keys':{}})
        private_json(keys.with_suffix('.capture.json'),{'state':'verified'})
        selected={'owner':'synthetic','root':str(self.work/'synthetic'/'db_storage'),'keys':''}
        backend.save_settings({'accounts':[{**selected,'keys':str(keys)}, {'owner':'other','root':'other','keys':'other-secret'}]})
        verified={**selected,'keys':str(keys),'nickname':'Synthetic'}
        with patch.object(backend,'account',return_value=selected),patch.object(backend,'main_processes',return_value=[]), \
                patch.object(backend,'ensure_closed'),patch.object(backend,'reverify_account',return_value=verified) as verify:
            result=backend.dispatch({'action':'capture','slot':'A','task_id':task['id'],'account':selected,'identity_confirmed':True,'executable':'synthetic.exe'})
        self.assertEqual(result['account']['nickname'],'Synthetic')
        self.assertEqual(Path(verify.call_args.args[0]['keys']).resolve(),keys.resolve())

    def test_cached_keys_never_attach_by_old_role_or_nickname(self):
        selected={'owner':'synthetic','root':str(self.work/'synthetic'/'db_storage'),'keys':''}
        backend.save_settings({'accounts':[{'owner':'other','root':selected['root'],'keys':'wrong-owner'},
                                           {**selected,'root':'different-root','keys':'wrong-root'}]})
        self.assertEqual(backend.cached_keys(selected),[])

    def test_running_account_with_valid_cached_material_does_not_recapture(self):
        task=backend.dispatch({'action':'task-new'})
        selected={'owner':'synthetic','root':'synthetic','keys':''}
        with patch.object(backend,'account',return_value=selected),patch.object(backend,'main_processes',return_value=[10]), \
                patch.object(backend,'cached_keys',return_value=['known-keys']),patch.object(backend,'reverify_material'), \
                patch.object(backend,'capture') as capture:
            with self.assertRaisesRegex(ValueError,'历史密钥.*正常退出'):
                backend.dispatch({'action':'capture','slot':'A','task_id':task['id'],'account':selected,'identity_confirmed':True,'executable':'synthetic.exe'})
            capture.assert_not_called()

    def test_verified_registry_is_per_account_not_source_target_role(self):
        selected={'owner':'synthetic','root':str(self.work/'synthetic'/'db_storage'),'keys':'first-key-path','nickname':'Synthetic'}
        backend.record_read({'slot':'A'},selected)
        backend.record_read({'slot':'B'},{**selected,'keys':'second-key-path'})
        registry=json.loads((self.work/'account-material.json').read_text())
        self.assertEqual(len(registry),1)
        self.assertEqual(next(iter(registry.values()))['keys'],'second-key-path')
        self.assertNotIn('enc_key',json.dumps(registry))

    def test_sqlcipher_failure_identifies_database_without_key_values(self):
        root=self.work/'synthetic'/'db_storage'; root.mkdir(parents=True)
        from chat_rescue.operations import ALL
        for rel in ALL:
            path=root/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'synthetic')
        with patch.object(backend.cli,'ensure_closed'),patch.object(backend.cli,'load_key',return_value='11'*32), \
                patch.object(backend.cli,'DB',side_effect=RuntimeError('SQLCipher query failed (code 26)')):
            with self.assertRaisesRegex(ValueError,r'message/message_0.db.*code 26'):
                backend.cli.verify_keys(root,'synthetic-keys')

    def test_reverify_rebinds_new_receipt_without_changing_old(self):
        root=self.work/'synthetic'/'db_storage'; root.mkdir(parents=True)
        rels=('message/message_0.db','message/message_resource.db','session/session.db','contact/contact.db')
        old=self.work/'jobs'/'old'; old.mkdir(parents=True)
        for rel in rels:
            path=root/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'synthetic-page')
        keys=old/'keys.json'; private_json(keys,{'keys':{rel:{'enc_key':'11'*32} for rel in rels}})
        receipt=keys.with_suffix('.capture.json'); private_json(receipt,{'state':'verified','file_version':'4.2.0.13',
              'owner':'synthetic','database_root':'Z:/old/synthetic/db_storage','verified_databases':list(rels),'verified_required_databases':4})
        before=receipt.read_bytes()
        selected={'owner':'synthetic','root':str(root),'keys':str(keys)}
        with patch.object(backend,'ensure_closed'),patch.object(backend.cli,'verify_keys',return_value={'verified_required_databases':4,'verified_databases':list(rels)}), \
                patch.object(backend,'account_nickname',return_value='Synthetic'),patch.object(backend,'verify_page_hmac'):
            result=backend.reverify_account(selected)
        new=Path(result['keys']); self.assertNotEqual(new,keys)
        self.assertEqual(receipt.read_bytes(),before)
        saved=json.loads(new.with_suffix('.capture.json').read_text())
        self.assertEqual(saved['database_root'],str(root.resolve()))
        self.assertEqual(saved['relocated_from_receipt_sha256'],digest(receipt))

    def old_inventory_material(self, include_extra_key):
        from chat_rescue.operations import ALL
        root=self.work/'synthetic'/'db_storage'; root.mkdir(parents=True)
        extra='message/message_7.db'; rels=(*ALL,extra)
        for rel in rels:
            path=root/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'synthetic-page')
        keys=self.work/'jobs'/'old'/'keys.json'; keys.parent.mkdir(parents=True); make_private(keys.parent)
        private_json(keys,{'keys':{rel:{'enc_key':'11'*32} for rel in (rels if include_extra_key else ALL)}})
        receipt=keys.with_suffix('.capture.json'); private_json(receipt,{'state':'verified','owner':'synthetic','file_version':'4.2.0.50',
                    'database_root':str(root),'verified_databases':list(ALL),'verified_required_databases':4})
        return {'root':str(root),'owner':'synthetic','keys':str(keys)},receipt,rels

    def test_old_inventory_is_refreshed_only_after_current_keys_and_integrity_pass(self):
        selected,receipt,rels=self.old_inventory_material(True); before=receipt.read_bytes()
        with patch.object(backend,'ensure_closed'),patch.object(backend,'verify_page_hmac') as hmac_check, \
                patch.object(backend.cli,'verify_keys',return_value={'verified_databases':list(rels),'verified_required_databases':5}) as integrity, \
                patch.object(backend,'account_nickname',return_value='Synthetic'):
            result=backend.reverify_account(selected)
        self.assertEqual(hmac_check.call_count,5); integrity.assert_called_once()
        new=json.loads(Path(result['keys']).with_suffix('.capture.json').read_text())
        self.assertEqual(new['verified_required_databases'],5)
        self.assertEqual(receipt.read_bytes(),before)

    def test_missing_new_shard_key_names_database_and_requires_login_not_read_loop(self):
        selected,receipt,rels=self.old_inventory_material(False)
        with patch.object(backend,'ensure_closed'),patch.object(backend,'verify_page_hmac'):
            with self.assertRaisesRegex(ValueError,r'message/message_7.db.*打开微信.*登录'):
                backend.reverify_account(selected)
        self.assertEqual(list(self.work.rglob('keys.pending.json')),[])

    def test_wrong_receipt_owner_still_cannot_be_rebound(self):
        selected,receipt,rels=self.old_inventory_material(True)
        with patch.object(backend,'ensure_closed'),patch.object(backend,'verify_page_hmac') as hmac_check:
            with self.assertRaisesRegex(ValueError,'身份不匹配'):
                backend.reverify_account({**selected,'owner':'different'})
            hmac_check.assert_not_called()

    def test_reverify_integrity_failure_does_not_create_success_receipt(self):
        selected={'owner':'synthetic','root':'unused','keys':'unused'}
        with patch.object(backend,'ensure_closed'),patch.object(backend,'require_private'), \
                patch.object(backend,'reverify_material',side_effect=ValueError('HMAC failed')):
            with self.assertRaises(ValueError): backend.reverify_account(selected)
        self.assertEqual(list(self.work.rglob('*.capture.json')),[])

    @unittest.skipUnless(os.name == 'nt', 'Windows encrypted relocation')
    def test_real_encrypted_keys_survive_changed_root_and_absolute_key_names(self):
        from chat_rescue.vendor.sqlcipher_probe import DB
        from chat_rescue.operations import ALL
        root=self.work/'old'/'synthetic'/'db_storage'; root.mkdir(parents=True)
        keys=self.work/'jobs'/'original'/'keys.json'; keys.parent.mkdir(parents=True); make_private(keys.parent)
        entries={}
        for rel in ALL:
            target=root/rel; target.parent.mkdir(parents=True,exist_ok=True); key=os.urandom(32).hex()
            with DB(target,key,fixture_write=True) as database:
                database.query('CREATE TABLE contact(username TEXT,nick_name TEXT);')
                database.query("INSERT INTO contact VALUES('synthetic','Synthetic');")
            entries[str(target)]={'enc_key':key}
        private_json(keys,{'keys':entries})
        private_json(keys.with_suffix('.capture.json'),{'state':'verified','owner':'synthetic',
                    'file_version':'4.2.0.13','database_root':str(root),'verified_required_databases':4,'verified_databases':list(ALL)})
        moved=self.work/'new computer'/'synthetic'/'db_storage'; moved.parent.mkdir(parents=True)
        shutil.move(str(root),str(moved))
        with patch.object(backend,'ensure_closed'),patch.object(backend.cli,'ensure_closed'):
            result=backend.reverify_account({'root':str(moved),'owner':'synthetic','keys':str(keys)})
        self.assertEqual(result['nickname'],'Synthetic')
        payload=json.loads(Path(result['keys']).read_text())
        self.assertEqual(set(payload['keys']),set(ALL))
        self.assertNotEqual(Path(result['keys']),keys)
