"""Date-range loss detection with synthetic rows, no live accounts or message bodies."""
from datetime import datetime,timezone
import json
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from test_workflow import fixture,db,A,B,FIELDS
from chat_rescue import history_audit as audit
from chat_rescue.workflow import private_json,digest,table_for
from chat_rescue.shards import database_rels,mutable_rels


class HistoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.task=Path(self.tmp.name)/'job/sync';self.task.mkdir(parents=True)
        self.a=self.task/'A-plain';self.b=self.task/'B-plain'
        fixture(self.a,False);fixture(self.b,True)
        self.early=int(datetime(2025,1,5,tzinfo=timezone.utc).timestamp())
        self.late=int(datetime(2026,8,5,tzinfo=timezone.utc).timestamp())
        with db(self.b/'message/message_0.db') as c:
            c.execute('UPDATE '+table_for(A)+' SET create_time=? WHERE server_id=101',(self.early,))
            c.execute('UPDATE '+table_for(A)+' SET create_time=? WHERE server_id=102',(self.late,))
        for root,owner in ((self.a,A),(self.b,B)):
            private_json(root/'export-report.json',{'account_directory':owner,'exported':{r:digest(root/r) for r in database_rels(root)}})
        recovered=self.task/'A-plan/recovered-plain';recovered.parent.mkdir();fixture(recovered,False)
        with db(recovered/'message/message_0.db') as c:
            c.execute('CREATE TABLE '+table_for(B)+'('+FIELDS+')')
            c.executemany('INSERT INTO '+table_for(B)+'(server_id,create_time,local_type) VALUES(?,?,1)',[(101,self.early),(102,self.late)])
        bundle=self.task/'B-chat';bundle.mkdir()
        private_json(bundle/'conversation.json',{'owner':B,'peer':A,'messages':[
            {'fields':{'server_id':sid,'create_time':ts,'local_type':1}} for sid,ts in ((101,self.early),(102,self.late))]})
        private_json(bundle/'manifest.json',{'sha256':digest(bundle/'conversation.json'),'message_count':2})
        private_json(self.task/'sync-plan.json',{'version':4,'mode':'replace-a-from-b','accounts':{
            'A':{'owner':A,'root':str(Path(self.tmp.name)/A/'db_storage')},
            'B':{'owner':B,'root':str(Path(self.tmp.name)/B/'db_storage')}}})
        candidates={}
        for rel in mutable_rels(recovered):
            path=self.task/'A-plan/candidates'/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'synthetic encrypted placeholder')
            candidates[rel]=digest(path)
        private_json(self.task/'A-plan/plan.json',{'candidates':candidates})
        private_json(self.task/'sync-result.json',{'state':'applied','accounts':{'A':{'state':'applied','after':candidates}}})

    def test_old_months_present_and_application_hashes_match(self):
        result,_=audit.audit_task(self.task)
        self.assertEqual(result['B_bundle']['first_date'],'2025-01-05')
        self.assertEqual(result['A_candidate']['first_date'],'2025-01-05')
        self.assertEqual(result['bundle_missing_from_candidate'],{})
        self.assertTrue(result['applied_A_hashes_match_candidates'])
        summary=json.dumps(result)
        for value in (A,B,'server_id','packed_info_data','message_content',str(self.task)):
            self.assertNotIn(value,summary)

    def test_missing_history_detected_even_when_total_row_count_is_equal(self):
        with db(self.task/'A-plan/recovered-plain/message/message_0.db') as c:
            c.execute('UPDATE '+table_for(B)+' SET server_id=999,create_time=? WHERE server_id=101',(self.late,))
        result,_=audit.audit_task(self.task)
        self.assertEqual(result['B_bundle']['rows'],result['A_candidate']['rows'])
        self.assertEqual(result['bundle_missing_from_candidate'],{'2025-01':1})

    def test_changed_timestamp_detected_with_identical_message_ids_and_counts(self):
        with db(self.task/'A-plan/recovered-plain/message/message_0.db') as c:
            c.execute('UPDATE '+table_for(B)+' SET create_time=? WHERE server_id=101',(self.late,))
        result,_=audit.audit_task(self.task)
        self.assertEqual(result['bundle_missing_from_candidate'],{})
        self.assertEqual(result['bundle_timestamps_changed_in_candidate'],[{'source_month':'2025-01','target_month':'2026-08','count':1}])

    def test_candidate_tampering_is_reported_and_export_tampering_refused(self):
        (self.task/'A-plan/candidates/message/message_0.db').write_bytes(b'changed')
        self.assertFalse(audit.audit_task(self.task)[0]['encrypted_candidates_match_plan'])
        with db(self.b/'message/message_0.db') as c:c.execute('UPDATE '+table_for(A)+' SET create_time=0')
        with self.assertRaisesRegex(ValueError,'hash'):audit.audit_task(self.task)

    def test_v5_voice_plan_dates_include_media_hash_without_claiming_playback(self):
        from chat_rescue.media import windows_capabilities
        for root in (self.a,self.b,self.task/'A-plan/recovered-plain'):
            with db(root/'message/media_0.db') as c:c.execute('CREATE TABLE VoiceInfo(synthetic INTEGER)')
            path=root/'export-report.json'
            if path.exists():
                value=json.loads(path.read_text(encoding='utf-8'))
                value['exported']['message/media_0.db']=digest(root/'message/media_0.db')
                path.write_text(json.dumps(value),encoding='utf-8')
        path=self.task/'sync-plan.json';manifest=json.loads(path.read_text(encoding='utf-8'))
        manifest.update(version=5,capabilities=windows_capabilities(True,False)+[
            'windows-single-chat-replacement','windows-verified-shard-routing'])
        path.write_text(json.dumps(manifest),encoding='utf-8')
        candidate=self.task/'A-plan/candidates/message/media_0.db';candidate.write_bytes(b'synthetic encrypted voice placeholder')
        path=self.task/'A-plan/plan.json';plan=json.loads(path.read_text(encoding='utf-8'))
        plan['candidates']['message/media_0.db']=digest(candidate)
        path.write_text(json.dumps(plan),encoding='utf-8')
        path=self.task/'sync-result.json';receipt=json.loads(path.read_text(encoding='utf-8'))
        receipt['accounts']['A']['after']['message/media_0.db']=digest(candidate)
        path.write_text(json.dumps(receipt),encoding='utf-8')
        result,_=audit.audit_task(self.task)
        self.assertTrue(result['applied_A_hashes_match_candidates'])
        self.assertTrue(result['snapshot_options']['include_voice'])
        self.assertFalse(result['client_media_playback_checked'])
        self.assertEqual(result['bundle_missing_from_candidate'],{})

    def test_fast_json_reads_only_selected_chat_without_resource_database(self):
        output=Path(self.tmp.name)/'local-json'
        (self.b/'message/message_resource.db').unlink()
        with db(self.b/'message/message_0.db') as c:
            c.execute('CREATE TABLE Msg_unrelated('+FIELDS+')')
            c.execute("INSERT INTO Msg_unrelated(server_id,message_content) VALUES(999,'unrelated body')")
            c.execute('UPDATE '+table_for(A)+' SET create_time=? WHERE server_id=101',(self.late+1,))
        before=digest(self.b/'message/message_0.db')
        self.assertEqual(audit.diagnostic_conversation(self.b,B,A,output),2)
        data=json.loads((output/'conversation.json').read_text(encoding='utf-8'))
        self.assertTrue(data['diagnostic_only']);self.assertTrue(data['not_a_restore_bundle'])
        self.assertEqual([m['fields']['server_id'] for m in data['messages']],[102,101])
        self.assertNotIn('unrelated body',json.dumps(data))
        self.assertFalse((output/'manifest.json').exists())
        self.assertEqual(digest(self.b/'message/message_0.db'),before)

    def test_failure_writes_report_and_progress_before_doing_any_snapshot(self):
        local=Path(self.tmp.name)/'local'
        with patch.dict(os.environ,{'LOCALAPPDATA':str(local)}), \
             patch.object(audit,'require_private',side_effect=RuntimeError('key file must be private (chmod 600)')), \
             patch.object(audit,'export_root') as export, redirect_stdout(io.StringIO()) as console:
            self.assertEqual(audit.main(),1)
        output=next((local/'ChatRescue/Desktop').glob('history-audit-*'))
        report=json.loads((output/'history-audit.json').read_text(encoding='utf-8'))
        self.assertEqual(report['audit_state'],'failed')
        self.assertEqual(report['stage'],'load_settings')
        self.assertEqual(report['error']['code'],'private_acl_failed')
        self.assertTrue((output/'progress.log').stat().st_size)
        self.assertIn('History Audit v3 starting',console.getvalue())
        export.assert_not_called()

    def test_unclassified_errors_do_not_include_sensitive_exception_text(self):
        detail=audit.error_details(ValueError('synthetic-private-key-and-account'),'saved_task')
        self.assertEqual(detail['code'],'unclassified_error')
        self.assertNotIn('synthetic-private',json.dumps(detail))


@unittest.skipUnless(os.name=='nt','Windows private-snapshot history audit')
class SnapshotAuditTests(unittest.TestCase):
    def test_actual_snapshot_jsons_and_hashes_without_changing_either_live_fixture(self):
        from test_replacement import ReplacementTests
        from chat_rescue.replacement import make_replace_plan
        from chat_rescue.sync import apply_sync_plan
        from chat_rescue import operations as ops
        helper=ReplacementTests();helper.setUp()
        self.addCleanup(helper.doCleanups);self.addCleanup(helper.tearDown)
        local=helper.root/'audit-local'
        workspace=ops.fresh(local/'ChatRescue/Desktop')
        task=workspace/'jobs/synthetic-task/sync'
        a,ak=helper.target;b,bk=helper.source
        accounts=[{'owner':A,'root':str(a),'keys':str(ak)}, {'owner':B,'root':str(b),'keys':str(bk)}]
        private_json(workspace/'settings.json',{'accounts':accounts})
        with patch('chat_rescue.operations.ensure_closed'),patch.object(audit,'ensure_closed'), \
             patch.dict(os.environ,{'LOCALAPPDATA':str(local)}),redirect_stdout(io.StringIO()):
            make_replace_plan(*accounts,task)
            apply_sync_plan(task)
            before_a,before_b=ops.state(a),ops.state(b)
            original_export=audit.export_root
            def export_after_early_report(*args,**kwargs):
                output=next(workspace.glob('history-audit-*'))
                early=json.loads((output/'history-audit.json').read_text(encoding='utf-8'))
                self.assertEqual(early['B_bundle']['rows'],2)
                self.assertEqual(early['audit_state'],'running')
                return original_export(*args,**kwargs)
            with patch.object(audit,'export_root',side_effect=export_after_early_report):
                self.assertEqual(audit.main(),0)
        self.assertEqual(ops.state(a),before_a);self.assertEqual(ops.state(b),before_b)
        folders=list(workspace.glob('history-audit-*'));self.assertEqual(len(folders),1)
        output=folders[0]
        report=json.loads((output/'history-audit.json').read_text(encoding='utf-8'))
        self.assertEqual(report['A_current_export_state'],'verified')
        self.assertEqual(report['audit_state'],'completed')
        self.assertEqual(report['bundle_missing_from_current_A'],{})
        self.assertEqual(report['bundle_timestamps_changed_in_current_A'],[])
        self.assertTrue((output/'A-candidate-chat/conversation.json').is_file())
        self.assertTrue((output/'A-current-chat/conversation.json').is_file())
        current=json.loads((output/'A-current-chat/conversation.json').read_text(encoding='utf-8'))
        self.assertEqual((current['owner'],current['peer']),(A,B))
        self.assertNotIn(A,json.dumps(report));self.assertNotIn(B,json.dumps(report))
        with patch.object(audit,'ensure_closed',side_effect=RuntimeError(
                '请从微信菜单正常退出所有微信（关闭窗口可能仍驻留托盘）后重试')), \
             patch.object(audit,'export_root') as export, \
             patch.dict(os.environ,{'LOCALAPPDATA':str(local)}),redirect_stdout(io.StringIO()):
            self.assertEqual(audit.main(),2)
            export.assert_not_called()
        second=next(folder for folder in workspace.glob('history-audit-*') if folder!=output)
        blocked=json.loads((second/'history-audit.json').read_text(encoding='utf-8'))
        self.assertEqual(blocked['audit_state'],'partial')
        self.assertEqual(blocked['A_current_export_error']['code'],'wechat_running')
        self.assertEqual(blocked['bundle_missing_from_candidate'],{})
        self.assertTrue((second/'A-candidate-chat/conversation.json').is_file())
        self.assertFalse((second/'A-current-plain').exists())
        self.assertEqual(ops.state(a),before_a);self.assertEqual(ops.state(b),before_b)
