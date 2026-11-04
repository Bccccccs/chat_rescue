"""Synthetic voice payloads: header validation is NOT a playback test."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_workflow import A, B, fixture, db
from chat_rescue import operations as ops, sync
from chat_rescue.workflow import (save_chat, prepare_plain_restore, table_for,
                                  digest, private_json, connect)
from chat_rescue.voice import MEDIA, CAPABILITY, HEADER
from chat_rescue.cipher import encrypt_database, export_database
from chat_rescue.vendor.sqlcipher_probe import DB, library

AUDIO1 = HEADER + b'\nsynthetic-one-not-real-audio'
AUDIO2 = HEADER + b'\nsynthetic-two-not-real-audio'


def voice_fixture(root, source):
    fixture(root, source)
    with db(root / MEDIA) as c:
        c.executescript("""CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY);
        CREATE TABLE VoiceInfo(chat_name_id INTEGER,create_time INTEGER,local_id INTEGER,
            svr_id INTEGER,voice_data BLOB,data_index TEXT DEFAULT '0');
        CREATE UNIQUE INDEX VoiceInfo_UNIQUE_INDEX ON VoiceInfo(chat_name_id,create_time,local_id,data_index);
        CREATE INDEX VoiceInfo_INDEX ON VoiceInfo(chat_name_id,svr_id);""")
        c.execute('INSERT INTO Name2Id(rowid,user_name) VALUES(57,?)', (A if source else 'wxid_foreign_demo',))
        if source:
            c.executemany('INSERT INTO VoiceInfo VALUES(57,?,?,?,?,\'0\')',
                          [(1700000000, 1, 101, AUDIO1), (1700000001, 2, 102, AUDIO2)])
    if source:
        with db(root / ops.RELS[0]) as c:
            c.execute('UPDATE ' + table_for(A) + ' SET local_type=34,status=3,download_status=1')
        with db(root / ops.RELS[1]) as c:
            c.execute('DELETE FROM MessageResourceDetail')
            c.execute('DELETE FROM MessageResourceInfo')


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.target, self.bundle = [self.root / n for n in ('source','target','bundle')]
        voice_fixture(self.source, True)
        voice_fixture(self.target, False)

    def save(self, **kw):
        return save_chat(self.source, B, A, self.bundle, include_voice=True, **kw)

    def restore(self, target=None, name='out'):
        return prepare_plain_restore(self.bundle, target or self.target, A, self.root/name, include_voice=True)

    def alter(self, sql, args=()):
        with db(self.source / MEDIA) as c:
            c.execute(sql, args)

    def test_both_directions_original_bytes_and_local_mapping(self):
        self.save()
        # Offset the target local IDs; media indices must follow the NEW message IDs.
        t = table_for(B)
        with db(self.target / ops.RELS[0]) as c:
            from test_workflow import FIELDS
            c.execute('CREATE TABLE ' + t + '(' + FIELDS + ')')
            c.execute('INSERT INTO '+t+'(local_id,server_id,real_sender_id) VALUES(80,999,4)')
        r = self.restore()
        self.assertEqual((r['added'],r['voice_added'],r['voice_repaired']), (2,2,0))
        with connect(self.root/'out'/MEDIA) as c:
            rows = c.execute('SELECT n.user_name,v.local_id,v.svr_id,v.voice_data FROM VoiceInfo v JOIN Name2Id n ON n.rowid=v.chat_name_id ORDER BY svr_id').fetchall()
            self.assertEqual([tuple(x) for x in rows], [(B,81,101,AUDIO1),(B,82,102,AUDIO2)])
            self.assertEqual(c.execute('SELECT rowid FROM Name2Id WHERE user_name=?', (B,)).fetchone()[0], 58)
        with connect(self.root/'out'/ops.RELS[0]) as c:
            rows=c.execute('SELECT server_id,real_sender_id,hex(packed_info_data),status FROM '+t+' WHERE local_type=34 ORDER BY server_id').fetchall()
            self.assertEqual([tuple(x) for x in rows],[(101,4,'080410013A0178',3),(102,302,'08AE021002',3)])

    def test_existing_message_missing_audio_repair_and_idempotence(self):
        self.save();self.restore()
        with db(self.root/'out'/MEDIA) as c:
            c.execute('UPDATE VoiceInfo SET voice_data=NULL WHERE svr_id=101')
            c.execute('DELETE FROM VoiceInfo WHERE svr_id=102')
        before = {r:digest(self.root/'out'/r) for r in ops.ALL}
        r=self.restore(self.root/'out','repair')
        self.assertEqual((r['added'],r['skipped'],r['voice_repaired']), (0,2,2))
        self.assertEqual(r['changed_databases'], [MEDIA])
        self.assertEqual(before, {r:digest(self.root/'repair'/r) for r in ops.ALL})
        r=self.restore(self.root/'repair','again')
        self.assertEqual((r['added'],r['voice_repaired'],r['voice_skipped']), (0,0,2))
        self.assertEqual(r['changed_databases'], [])

    def test_source_missing_can_use_complete_target_but_both_missing_block(self):
        self.save();self.restore();shutil.rmtree(self.bundle)
        self.alter('UPDATE VoiceInfo SET voice_data=NULL')
        self.save(allow_missing_voice=True)
        r=self.restore(self.root/'out','keep')
        self.assertEqual(r['voice_skipped'],2)
        with self.assertRaisesRegex(ValueError,'双方语音'):
            self.restore(name='both-missing')

    def test_missing_source_public_export_is_blocked(self):
        self.alter('UPDATE VoiceInfo SET voice_data=NULL WHERE svr_id=101')
        with self.assertRaisesRegex(ValueError,'源语音'):
            self.save()
        self.assertFalse((self.bundle/'manifest.json').exists())

    def test_different_nonempty_audio_rejected(self):
        self.save();self.restore()
        with db(self.root/'out'/MEDIA) as c:
            c.execute('UPDATE VoiceInfo SET voice_data=? WHERE svr_id=101',(AUDIO2,))
        with self.assertRaisesRegex(ValueError,'collision'):
            self.restore(self.root/'out','conflict')

    def test_unknown_indices_local_ids_times_format_and_multiline_rejected(self):
        for sql,args in [
            ("UPDATE VoiceInfo SET data_index='1' WHERE svr_id=101",()),
            ('UPDATE VoiceInfo SET local_id=99 WHERE svr_id=101',()),
            ('UPDATE VoiceInfo SET create_time=99 WHERE svr_id=101',()),
            ('UPDATE VoiceInfo SET voice_data=? WHERE svr_id=101',(b'bad-audio',)),
            ("INSERT INTO VoiceInfo SELECT chat_name_id,create_time,local_id+1,svr_id,voice_data,data_index FROM VoiceInfo WHERE svr_id=101",()),
        ]:
            with self.subTest(sql=sql):
                backup=self.root/'media-backup';shutil.copy2(self.source/MEDIA,backup)
                self.alter(sql,args)
                with self.assertRaises(ValueError):self.save()
                if self.bundle.exists():shutil.rmtree(self.bundle)
                shutil.copy2(backup,self.source/MEDIA)

    def test_other_conversation_same_id_is_preserved(self):
        with db(self.target/MEDIA) as c:
            c.execute("INSERT INTO VoiceInfo VALUES(57,1700000000,1,101,?,'0')",(AUDIO2,))
        self.save();self.restore()
        with db(self.root/'out'/MEDIA) as c:
            self.assertEqual(c.execute('SELECT voice_data FROM VoiceInfo WHERE chat_name_id=57').fetchone()[0],AUDIO2)
            self.assertEqual(c.execute('SELECT count(*) FROM VoiceInfo').fetchone()[0],3)

    def test_v1_cannot_carry_voice_and_v2_requires_opt_in(self):
        with self.assertRaises(ValueError):save_chat(self.source,B,A,self.bundle)
        self.save()
        with self.assertRaises(ValueError):prepare_plain_restore(self.bundle,self.target,A,self.root/'reject')

    def rewrite(self, edit):
        p=self.bundle/'conversation.json';data=json.loads(p.read_text());edit(data);p.write_text(json.dumps(data))
        m=self.bundle/'manifest.json';value=json.loads(m.read_text());value['sha256']=digest(p);m.write_text(json.dumps(value))

    def test_tampered_audio_path_links_and_duplicate_entries_blocked(self):
        self.save()
        original=(self.bundle/'conversation.json').read_text()
        for name in ('../secret','/tmp/test','voice/../secret','voice/000001.silk/other'):
            with self.subTest(name=name):
                self.rewrite(lambda d:d['voices'][0]['audio'].update(path=name))
                with self.assertRaises(ValueError):self.restore(name='bad')
                (self.bundle/'conversation.json').write_text(original)
        self.rewrite(lambda d:d['voices'].append(d['voices'][0]))
        with self.assertRaises(ValueError):self.restore(name='dup')

    def test_audio_hash_and_symlink_rejected(self):
        self.save();path=self.bundle/'voice/000001.silk'
        path.write_bytes(AUDIO2)
        with self.assertRaises(ValueError):self.restore()
        path.unlink()
        try:path.symlink_to(self.bundle/'voice/000002.silk')
        except OSError as e:
            if getattr(e,'winerror',None)==1314:self.skipTest('Windows symlink privilege unavailable')
            raise
        with self.assertRaises(ValueError):self.restore(name='link')

    def test_media_missing_shard_and_schema_blocked(self):
        self.save()
        (self.target/'message/media_1.db').write_bytes(b'new')
        with self.assertRaises(ValueError):self.restore()
        (self.target/'message/media_1.db').unlink()
        with db(self.target/MEDIA) as c:c.execute('ALTER TABLE VoiceInfo ADD COLUMN unknown TEXT')
        with self.assertRaises(ValueError):self.restore()

    def test_unknown_message_state_preserved_and_blocked(self):
        with db(self.source/ops.RELS[0]) as c:c.execute('UPDATE '+table_for(A)+' SET status=2')
        with self.assertRaisesRegex(ValueError,'状态尚未适配'):self.save()

    def test_windows_profile_is_rejected(self):
        private_json(self.source/'direction-profile.json',{'direction_profile':'windows-4.2.0.12-text'})
        with self.assertRaises(ValueError):self.save()


@unittest.skipIf(os.name == 'nt', 'Mac voice integration; Windows explicitly rejects voice mode')
class EncryptedVoiceTests(unittest.TestCase):
    def setUp(self):
        try:library()
        except (RuntimeError,OSError):self.skipTest('SQLCipher required')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        gate=patch('chat_rescue.operations.ensure_closed');gate.start();self.addCleanup(gate.stop)
        gate=patch('chat_rescue.operations.supported_host');gate.start();self.addCleanup(gate.stop)
        self.a=self.encrypted(A,False);self.b=self.encrypted(B,True)

    def encrypted(self,owner,source):
        plain=self.root/(owner+'-plain');voice_fixture(plain,source)
        live=self.root/owner/'db_storage';keys=self.root/(owner+'-keys.json');manifest={}
        for rel in ops.files_for(True):
            path=live/rel;path.parent.mkdir(parents=True,exist_ok=True)
            key=os.urandom(32).hex();manifest[rel]={'enc_key':key}
            with DB(path,key,fixture_write=True) as c:c.query('CREATE TABLE template(id INTEGER PRIMARY KEY);')
        private_json(keys,{'keys':manifest})
        for rel in ops.files_for(True):
            candidate=Path(str(live/rel)+'.candidate');encrypt_database(plain/rel,candidate,live/rel,keys);os.replace(candidate,live/rel)
        return {'owner':owner,'root':str(live),'keys':str(keys)}

    def plan(self):
        return sync.make_sync_plan(self.a,self.b,self.root/'sync',include_voice=True)

    def test_encrypted_union_apply_readback_and_complete_undo(self):
        before={slot:ops.state(a['root'],True) for slot,a in [('A',self.a),('B',self.b)]}
        salt=(Path(self.a['root'])/MEDIA).read_bytes()[:16]
        r=self.plan();self.assertEqual(r['directions']['A']['voice_added'],2)
        plan=json.loads((self.root/'sync/A-plan/plan.json').read_text())
        self.assertEqual(plan['version'],2);self.assertIn(MEDIA,plan['write_set'])
        sync.apply_sync_plan(self.root/'sync')
        self.assertEqual((Path(self.a['root'])/MEDIA).read_bytes()[:16],salt)
        export_database(Path(self.a['root'])/MEDIA,self.root/'read.db',self.a['keys'])
        with connect(self.root/'read.db') as c:
            self.assertEqual(c.execute('SELECT voice_data FROM VoiceInfo WHERE svr_id=101').fetchone()[0],AUDIO1)
        # Readback may create SHM; use plan receipt to check drift before undo.
        after=ops.state(self.a['root'],True)
        receipt=json.loads((self.root/'sync/sync-result.json').read_text())
        self.assertEqual(after,receipt['accounts']['A']['after'])
        sync.undo_sync_plan(self.root/'sync')
        for slot,a in [('A',self.a),('B',self.b)]:self.assertEqual(ops.state(a['root'],True),before[slot])

    def test_media_wal_new_shard_or_candidate_tamper_blocks_both(self):
        self.plan();before=ops.state(self.a['root'],True)
        wal=Path(self.b['root'])/(MEDIA+'-wal');wal.write_bytes(b'changed')
        with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')
        self.assertEqual(ops.state(self.a['root'],True),before);wal.unlink()
        extra=Path(self.b['root'])/'message/media_1.db';extra.write_bytes(b'extra')
        with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')
        extra.unlink()
        extra=Path(self.b['root'])/'message/message_1.db';extra.write_bytes(b'new message shard')
        with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')
        extra.unlink()
        candidate=self.root/'sync/A-plan/candidates'/MEDIA;candidate.write_bytes(b'tampered')
        with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')
        self.assertFalse((self.root/'sync/sync-started.json').exists())

    def test_media_replace_failure_restores_all_originals_and_sidecars(self):
        self.plan();before=ops.state(self.a['root'],True);real=os.replace
        def fail(a,b):
            if str(a).endswith('media_0.db.chat-rescue-stage'):raise OSError('synthetic media failure')
            return real(a,b)
        with patch('chat_rescue.operations.os.replace',side_effect=fail):
            with self.assertRaises(RuntimeError):sync.apply_sync_plan(self.root/'sync')
        self.assertEqual(ops.state(self.a['root'],True),before)
        receipt=json.loads((self.root/'sync/A-plan/rollback.json').read_text())
        self.assertEqual(receipt['state'],'rolled_back')

    def test_missing_media_key_rejects_export(self):
        keys=Path(self.a['keys']);v=json.loads(keys.read_text());v['keys'].pop(MEDIA);keys.write_text(json.dumps(v))
        with self.assertRaises(RuntimeError):ops.export_root(self.a['root'],keys,self.root/'missing-key',include_voice=True)

    def test_plan_cannot_expand_write_paths(self):
        self.plan();p=self.root/'sync/A-plan/plan.json';v=json.loads(p.read_text());v['write_set'].append('../outside');p.write_text(json.dumps(v))
        with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')
        self.assertFalse((self.root/'sync/sync-started.json').exists())

    def test_media_change_after_apply_blocks_undo(self):
        self.plan();sync.apply_sync_plan(self.root/'sync')
        p=Path(self.a['root'])/MEDIA;p.write_bytes(p.read_bytes()+b'change')
        with self.assertRaises(ValueError):sync.undo_sync_plan(self.root/'sync')
        self.assertFalse((self.root/'sync/undo-started.json').exists())

class VoiceResourceTests(unittest.TestCase):
    setUp = VoiceTests.setUp
    save = VoiceTests.save
    restore = VoiceTests.restore
    def add_resource(self):
        with db(self.source/ops.RELS[1]) as c:
            c.execute('INSERT INTO MessageResourceInfo VALUES(8,11,13,34,1700000000,1,101,6,?)',(b'synthetic opaque voice metadata',))
            c.execute("INSERT INTO MessageResourceDetail VALUES(16,8,1,10,1700000000,0,0,'0',NULL)")

    def test_voice_resource_creation_dedup_repair_and_collision(self):
        self.add_resource();self.save();r=self.restore()
        with connect(self.root/'out'/ops.RELS[1]) as c:
            row=c.execute('SELECT r.message_local_id,ch.user_name,s.user_name FROM MessageResourceInfo r JOIN ChatName2Id ch ON ch.rowid=r.chat_id JOIN SenderName2Id s ON s.rowid=r.sender_id').fetchone()
            self.assertEqual(tuple(row),(1,B,A))
        r=self.restore(self.root/'out','dedup');self.assertEqual(r['changed_databases'],[])
        with db(self.root/'out'/ops.RELS[1]) as c:
            c.execute('DELETE FROM MessageResourceDetail');c.execute('DELETE FROM MessageResourceInfo')
        r=self.restore(self.root/'out','resource-repair')
        self.assertEqual(r['added'],0);self.assertEqual(r['voice_repaired'],0)
        self.assertEqual(r['changed_databases'],[ops.RELS[1]])
        with db(self.root/'dedup'/ops.RELS[1]) as c:c.execute('UPDATE MessageResourceInfo SET packed_info=?',(b'conflicting',))
        with self.assertRaisesRegex(ValueError,'collision'):self.restore(self.root/'dedup','bad-resource')

    def test_foreign_source_voice_resource_refused(self):
        self.add_resource()
        with db(self.source/ops.RELS[1]) as c:c.execute('UPDATE ChatName2Id SET user_name=?',('wxid_foreign_demo',))
        with self.assertRaisesRegex(ValueError,'source identity collision'):self.save()


@unittest.skipIf(os.name == 'nt', 'Mac voice integration; Windows explicitly rejects voice mode')
class VoiceIntegrationCases(unittest.TestCase):
    setUp = EncryptedVoiceTests.setUp
    encrypted = EncryptedVoiceTests.encrypted
    plan = EncryptedVoiceTests.plan
    def write_live(self, account, plain, rels):
        for rel in rels:
            live=Path(account['root'])/rel;candidate=Path(str(live)+'.test-candidate')
            encrypt_database(Path(plain)/rel,candidate,live,account['keys']);os.replace(candidate,live)

    def test_complementary_audio_repair_both_sides_no_new_messages(self):
        bp=self.root/(B+'-plain');ap=self.root/(A+'-plain')
        save_chat(bp,B,A,self.root/'seed',include_voice=True)
        prepare_plain_restore(self.root/'seed',ap,A,self.root/'a-full',include_voice=True)
        with db(self.root/'a-full'/MEDIA) as c:c.execute('UPDATE VoiceInfo SET voice_data=NULL WHERE svr_id=102')
        with db(bp/MEDIA) as c:c.execute('UPDATE VoiceInfo SET voice_data=NULL WHERE svr_id=101')
        self.write_live(self.a,self.root/'a-full',ops.files_for(True));self.write_live(self.b,bp,[MEDIA])
        result=self.plan()
        for slot in ('A','B'):
            self.assertEqual((result['directions'][slot]['added'],result['directions'][slot]['voice_repaired']),(0,1))
            p=json.loads((self.root/('sync/'+slot+'-plan/plan.json')).read_text())
            self.assertEqual(p['write_set'],[MEDIA])
        sync.apply_sync_plan(self.root/'sync')
        result=sync.make_sync_plan(self.a,self.b,self.root/'second',include_voice=True)
        self.assertTrue(all(d['state']=='unchanged' for d in result['directions'].values()))
        self.assertTrue(all(d['voice_skipped']==2 for d in result['directions'].values()))

    def test_cli_voice_export_save_restore_apply_and_five_key_verification(self):
        import sys,contextlib,io
        from chat_rescue import __main__ as cli
        def invoke(*args):
            out=io.StringIO()
            with patch.object(sys,'argv',['chat-rescue',*map(str,args)]),contextlib.redirect_stdout(out),patch.object(cli,'ensure_closed'),patch.object(cli,'supported_host'):
                rc=cli.main()
            self.assertEqual(rc,0);return json.loads(out.getvalue())
        r=invoke('verify-keys','--db-root',self.b['root'],'--keys',self.b['keys'],'--include-voice')
        self.assertEqual(r['verified_required_databases'],5)
        r=invoke('export-db','--db-root',self.b['root'],'--keys',self.b['keys'],'--output',self.root/'cli-export','--include-voice')
        self.assertEqual(r['database_count'],5)
        invoke('save-chat','--plain-root',self.root/'cli-export','--owner',B,'--peer',A,'--output',self.root/'cli-bundle','--include-voice')
        r=invoke('plan-restore','--source',self.root/'cli-bundle','--target-db-root',self.a['root'],'--target-keys',self.a['keys'],'--target-owner',A,'--output',self.root/'cli-plan','--include-voice')
        self.assertEqual(r['voice_added'],2)
        self.assertEqual(invoke('apply','--plan',self.root/'cli-plan')['state'],'applied')

    def test_actual_process_interruption_keeps_receipts_and_refuses_retry(self):
        import subprocess,sys
        self.plan();directory=self.root/'sync/A-plan'
        script='''import os,sys
from unittest.mock import patch
from chat_rescue.operations import apply_plan
real=os.replace
def replace(a,b):
    real(a,b)
    if str(a).endswith('media_0.db.chat-rescue-stage'):os._exit(59)
with patch('chat_rescue.operations.os.replace',side_effect=replace),patch('chat_rescue.operations.ensure_closed'),patch('chat_rescue.operations.supported_host'):
    apply_plan(sys.argv[1])
'''
        result=subprocess.run([sys.executable,'-c',script,str(directory)],capture_output=True,text=True)
        self.assertEqual(result.returncode,59,result.stderr)
        self.assertTrue((directory/'apply-started.json').exists())
        self.assertFalse((directory/'applied.json').exists())
        self.assertFalse((directory/'rollback.json').exists())
        journal=[json.loads(line) for line in (directory/'apply-journal.jsonl').read_text().splitlines()]
        self.assertEqual(journal[-1],{'state':'replace_intent','database':MEDIA})
        for rel in ops.files_for(True):self.assertTrue((directory/'originals'/rel).exists())
        with self.assertRaises(ValueError):ops.apply_plan(directory)

    def test_parent_symlink_candidate_and_media_schema_are_rejected(self):
        self.plan();candidates=self.root/'sync/A-plan/candidates';moved=self.root/'moved-message'
        (candidates/'message').rename(moved);(candidates/'message').symlink_to(moved,target_is_directory=True)
        with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')
        self.assertFalse((self.root/'sync/sync-started.json').exists())

    def test_v1_bundle_stays_compatible_with_voice_mode(self):
        # Legacy payload is deliberately generated from a separate all-text fixture.
        plain=self.root/'legacy';fixture(plain,True)
        save_chat(plain,B,A,self.root/'legacy-bundle')
        r=ops.make_plan(self.root/'legacy-bundle',self.a['root'],self.a['keys'],A,self.root/'legacy-plan',include_voice=True)
        self.assertEqual(r['added'],2);self.assertEqual(r['voice_added'],0)
        self.assertEqual(ops.apply_plan(self.root/'legacy-plan')['state'],'applied')


@unittest.skipIf(os.name == 'nt', 'Mac capture completion groups')
class VoiceCaptureTests(unittest.TestCase):
    setUp = EncryptedVoiceTests.setUp
    encrypted = EncryptedVoiceTests.encrypted

    def simulate_capture(self, count):
        import argparse,subprocess
        from chat_rescue import __main__ as cli
        original=self.root/'original.app';copy=self.root/'copy.app'
        for app in (original,copy):
            exe=app/'Contents/MacOS/WeChat';exe.parent.mkdir(parents=True);exe.write_bytes(b'synthetic executable')
        private_json(copy.with_suffix('.prepare.json'),{'original':str(original),'signature':'synthetic signature','copy_executable_sha256':digest(copy/'Contents/MacOS/WeChat'),'original_executable_sha256':digest(original/'Contents/MacOS/WeChat')})
        output=self.root/'capture.json'
        args=argparse.Namespace(app=str(copy),original_app=str(original),db_root=self.b['root'],output=str(output),timeout=30,include_voice=True)
        def system(args):return '/synthetic/lldb' if args[0]=='/usr/bin/lldb' else 'synthetic signature'
        def provider(args,**kwargs):
            if args[0]=='/usr/bin/python3':
                keys=json.loads(Path(self.b['keys']).read_text())['keys']
                private_json(output,{'keys':{rel:keys[rel] for rel in ops.files_for(True)[:count]}})
                groups=json.loads(Path(args[args.index('--completion-groups')+1]).read_text())
                self.assertEqual(groups,[list(ops.files_for(True))])
            return subprocess.CompletedProcess(args,0)
        with patch.object(cli,'app_info',side_effect=lambda path:(Path(path),Path(path)/'Contents/MacOS/WeChat')),patch.object(cli,'run',side_effect=system),patch.object(cli,'mac_only'),patch.object(cli,'ensure_closed'),patch.object(cli.subprocess,'run',side_effect=provider):
            return cli.capture(args)

    def test_capture_requires_media_key_and_five_integrity_checks(self):
        r=self.simulate_capture(5)
        self.assertEqual(r['verified_required_databases'],5)
        self.assertEqual(r['accounts'][0]['owner'],B)

    def test_four_keys_cannot_complete_voice_capture(self):
        with self.assertRaises(RuntimeError):self.simulate_capture(4)

    def test_desktop_capture_receipt_cannot_advance_with_only_four_keys(self):
        from chat_rescue import desktop_backend as backend
        payload=self.root/'capture-request.json'
        private_json(payload,{'output':self.b['keys'],'include_voice':True})
        receipt=self.root/'capture-result.json'
        private_json(receipt,{'state':'complete','verified_required_databases':4})
        self.assertEqual(backend.capture_status(payload)['state'],'failed')
        receipt.unlink();private_json(receipt,{'state':'complete','verified_required_databases':5})
        self.assertEqual(backend.capture_status(payload)['state'],'complete')

    def test_desktop_reuses_valid_media_keys_but_requests_missing_key(self):
        from chat_rescue import desktop_backend as backend
        with patch.object(backend,'new_job',return_value=self.root/'inspection'):
            r=backend.inspect_account(self.b,include_voice=True)
        self.assertEqual(r['conversations'][0]['unsupported'],0)
        keys=Path(self.b['keys']);data=json.loads(keys.read_text());data['keys'].pop(MEDIA);keys.write_text(json.dumps(data))
        self.assertEqual(backend.inspect_account(self.b,include_voice=True),{'requires_voice_keys':True})
