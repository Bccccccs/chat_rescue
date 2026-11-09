"""Private task transfer with synthetic files only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from chat_rescue import portable_task as tasks
from chat_rescue.runtime import make_private, require_private


class PortableTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.work = self.base / 'old'; self.work.mkdir(); make_private(self.work)
        self.task = tasks.create(self.work)
        self.job = self.work / 'jobs' / 'synthetic'; self.job.mkdir(parents=True)
        self.keys = self.job / 'keys.json'; self.keys.write_text('{"keys":{}}')
        self.receipt = self.job / 'keys.capture.json'; self.receipt.write_text('{"database_root":"Z:/old","state":"verified"}')
        for p in (self.job, self.keys, self.receipt): make_private(p)

    def partial(self):
        return tasks.update(self.work, self.task['id'], accounts=[{'root':'Z:/old','owner':'synthetic','keys':str(self.keys)}, {}],
                            errors=['', 'synthetic failure'], include_voice=True)

    def test_partial_task_roundtrip_preserves_receipt_and_invalidates_verification(self):
        self.partial()
        unrelated = self.work / 'jobs' / 'unrelated'; unrelated.mkdir(); (unrelated / 'private.txt').write_text('do not copy')
        output = self.base / 'transfer'
        tasks.export_task(self.work, self.task['id'], output)
        self.assertFalse(any(p.name == 'private.txt' for p in output.rglob('*')))
        imported = tasks.import_task(self.base / 'new', output)
        self.assertNotEqual(imported['id'], self.task['id'])
        self.assertTrue(imported['relocated'])
        self.assertEqual(imported['errors'][1], 'synthetic failure')
        self.assertEqual(Path(imported['accounts'][0]['keys']).with_suffix('.capture.json').read_bytes(), self.receipt.read_bytes())
        require_private(Path(imported['accounts'][0]['keys']))
        self.assertTrue(imported['include_voice'])
        self.assertNotEqual(tasks.import_task(self.base / 'new', output)['id'], imported['id'])

    def test_export_rejects_arbitrary_account_and_plan_paths(self):
        external = self.base / 'outside'; external.mkdir(); (external / 'keys.json').write_text('private')
        tasks.update(self.work, self.task['id'], accounts=[{'keys':str(external / 'keys.json')},{}])
        with self.assertRaises(ValueError): tasks.export_task(self.work, self.task['id'], self.base/'bad')
        tasks.update(self.work, self.task['id'], accounts=[{},{}], plan=str(external))
        with self.assertRaises(ValueError): tasks.export_task(self.work, self.task['id'], self.base/'bad2')

    def test_tampered_extra_and_traversal_files_are_rejected(self):
        self.partial(); output=self.base/'transfer'; tasks.export_task(self.work,self.task['id'],output)
        manifest=output/'transfer.json'; original=manifest.read_text()
        payload=json.loads(original); key=next(iter(payload['files']))
        target=output/key; raw=target.read_bytes(); target.write_bytes(b'tampered')
        with self.assertRaises(ValueError): tasks.import_task(self.base/'new',output)
        target.write_bytes(raw); extra=output/'extra'; extra.write_text('extra')
        with self.assertRaises(ValueError): tasks.import_task(self.base/'new',output)
        extra.unlink(); payload['files']['../escape']='0'*64; manifest.write_text(json.dumps(payload))
        with self.assertRaises(ValueError): tasks.import_task(self.base/'new',output)

    def test_plan_history_and_started_markers_remain_immutable(self):
        plan=self.job/'sync'; plan.mkdir(); (plan/'sync-plan.json').write_text('{"target_root":"Z:/old"}')
        (plan/'sync-started.json').write_text('{"state":"started"}')
        tasks.update(self.work,self.task['id'],plan=str(plan))
        output=self.base/'transfer'; tasks.export_task(self.work,self.task['id'],output)
        imported=tasks.import_task(self.base/'new',output)
        self.assertTrue(imported['history_only'])
        self.assertEqual((Path(imported['history_plan'])/'sync-started.json').read_bytes(),(plan/'sync-started.json').read_bytes())
        self.assertEqual(imported['plan'],'')

    def test_changed_account_cannot_export_previous_accounts_plan(self):
        plan=self.job/'sync'; plan.mkdir()
        (plan/'sync-plan.json').write_text(json.dumps({'accounts':{'A':{'owner':'previous-a'},'B':{'owner':'previous-b'}}}))
        tasks.update(self.work,self.task['id'],accounts=[{'owner':'new-a'},{'owner':'new-b'}],plan=str(plan))
        with self.assertRaisesRegex(ValueError,'账号.*改变'):
            tasks.export_task(self.work,self.task['id'],self.base/'mixed')

    def test_imported_plans_block_low_level_apply_and_undo_before_live_access(self):
        from chat_rescue import operations as ops,sync
        plan=self.job/'sync';(plan/'A-plan').mkdir(parents=True)
        (plan/'sync-plan.json').write_text('{}');(plan/'A-plan/plan.json').write_text('{}')
        tasks.update(self.work,self.task['id'],plan=str(plan));output=self.base/'transfer'
        tasks.export_task(self.work,self.task['id'],output)
        imported=tasks.import_task(self.base/'new',output);history=Path(imported['history_plan'])
        with patch.object(ops,'supported_host'),patch.object(sync,'supported_host'), \
                patch.object(ops,'ensure_closed') as closed,patch.object(ops,'exclusive_lock') as lock, \
                patch.object(sync,'exclusive_lock') as sync_lock:
            for action,path in ((ops.apply_plan,history/'A-plan'),(sync.apply_sync_plan,history),(sync.undo_sync_plan,history)):
                with self.subTest(action=action.__name__),self.assertRaisesRegex(ValueError,'迁入.*历史'):
                    action(path)
            closed.assert_not_called();lock.assert_not_called();sync_lock.assert_not_called()

    def test_empty_task_does_not_export_and_invalid_ids_cannot_escape(self):
        with self.assertRaises(ValueError): tasks.export_task(self.work,self.task['id'],self.base/'empty')
        with self.assertRaises(ValueError): tasks.load(self.work,'../escape')

    def test_failed_report_is_transferred_and_paths_relocated(self):
        failure=self.job/'keys.capture-failed.json'; failure.write_text('{"verified_count":0}'); make_private(failure)
        tasks.update(self.work,self.task['id'],errors=['failed',''],failure_reports=[str(failure),''])
        output=self.base/'failed-transfer'; tasks.export_task(self.work,self.task['id'],output)
        imported=tasks.import_task(self.base/'new',output)
        self.assertEqual(Path(imported['failure_reports'][0]).read_bytes(),failure.read_bytes())

    def test_interrupted_import_is_guarded_before_copying_any_old_plan(self):
        plan=self.job/'sync'; plan.mkdir(); (plan/'sync-plan.json').write_text('{}')
        tasks.update(self.work,self.task['id'],plan=str(plan)); output=self.base/'transfer'
        tasks.export_task(self.work,self.task['id'],output)
        def fail_copy(*args,**kwargs): raise OSError('synthetic interrupted copy')
        with patch.object(tasks.shutil,'copyfileobj',side_effect=fail_copy):
            with self.assertRaises(OSError): tasks.import_task(self.base/'new',output)
        imported=next((self.base/'new'/'tasks').iterdir())
        self.assertTrue((imported/'migration-history.json').is_file())
        self.assertTrue(tasks.historical_plan(imported/'artifacts'/'plan'))

    def test_case_collisions_and_links_are_rejected(self):
        self.partial(); output=self.base/'transfer'; tasks.export_task(self.work,self.task['id'],output)
        manifest=output/'transfer.json'; payload=json.loads(manifest.read_text()); key=next(iter(payload['files']))
        payload['files'][key.upper()]=payload['files'][key]; manifest.write_text(json.dumps(payload))
        with self.assertRaises(ValueError): tasks.import_task(self.base/'new',output)
        try: (self.job/'link').symlink_to(self.base, target_is_directory=True)
        except OSError: return
        tasks.update(self.work,self.task['id'],plan=str(self.job))
        with self.assertRaises(ValueError): tasks.export_task(self.work,self.task['id'],self.base/'linked')
