"""Windows media candidates on synthetic encrypted accounts, never client tests."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_workflow import A, B, db
import test_replacement
from test_attachments import attachment_fixture, PNG, VIDEO, TEXT, TOKEN, VTOKEN, FILE, put
from test_voice import voice_fixture, AUDIO1
from chat_rescue import attachments as att, operations as ops, sync
from chat_rescue.cipher import encrypt_database, export_database
from chat_rescue.media import windows_options, windows_capabilities
from chat_rescue.replacement import make_replace_plan
from chat_rescue.voice import MEDIA
from chat_rescue.vendor.sqlcipher_probe import DB
from chat_rescue.workflow import table_for, private_json, digest


@unittest.skipUnless(os.name == 'nt', 'Windows media experiment')
class WindowsMediaTests(unittest.TestCase):
    encrypted = test_replacement.ReplacementTests.encrypted
    install = test_replacement.ReplacementTests.install
    tearDown = test_replacement.ReplacementTests.tearDown

    def setUp(self):
        test_replacement.ReplacementTests.setUp(self)
        media = self.root/'media-fixture'
        attachment_fixture(media, self.source[0].parent, True)
        for rel in ops.ALL:
            shutil.copy2(media/rel, self.b_plain/rel)
        with db(self.b_plain/ops.ALL[0]) as c:
            table=table_for(A)
            row=list(c.execute('SELECT * FROM '+table+' WHERE local_id=2').fetchone())
            row[0],row[1],row[2],row[5]=4,104,34,1700000003;row[3]+=3000
            c.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in row)+')',row)
            c.execute('UPDATE '+table+' SET packed_info_data=? WHERE real_sender_id=7',(bytes.fromhex('08071009'),))
            c.execute('UPDATE '+table+' SET packed_info_data=? WHERE real_sender_id=9',(bytes.fromhex('08091007'),))
        voice_fixture(self.root/'voice-template',False)
        for account,plain,owner in ((self.source,self.b_plain,B),(self.target,self.a_plain,A)):
            shutil.copy2(self.root/'voice-template'/MEDIA,plain/MEDIA)
            with db(plain/MEDIA) as c:
                c.execute('INSERT INTO Name2Id(rowid,user_name) VALUES(58,?)',(A if owner==B else B,))
                if owner==B:
                    c.execute("INSERT INTO VoiceInfo VALUES(58,1700000003,4,104,?,'0')",(AUDIO1,))
                else:
                    c.execute("INSERT INTO VoiceInfo VALUES(58,1600000000,20,900,?,'0')",(AUDIO1,))
                    c.execute("INSERT INTO VoiceInfo VALUES(57,1600000001,21,901,?,'0')",(AUDIO1,))
            root,keys=account
            value=json.loads(keys.read_text(encoding='utf-8'))
            key=os.urandom(32).hex();value['keys'][MEDIA]={'enc_key':key};keys.write_text(json.dumps(value),encoding='utf-8')
            with DB(root/MEDIA,key,fixture_write=True) as c:c.query('CREATE TABLE template(id INTEGER PRIMARY KEY)')
            self.install(account,plain)
            self.encrypt_media(account,plain)
            keys.with_suffix('.capture.json').write_text(json.dumps({'state':'verified','owner':owner,
                'database_root':str(root),'file_version':'4.2.0.13',
                'verified_required_databases':5,'verified_databases':list(ops.inspection_files(root,True))}),encoding='utf-8')
        self.a_before,self.b_before=ops.state(self.target[0],True),ops.state(self.source[0],True)

    def encrypt_media(self,account,plain):
        root,keys=account;candidate=Path(str(root/MEDIA)+'.media-test')
        encrypt_database(plain/MEDIA,candidate,root/MEDIA,keys);os.replace(candidate,root/MEDIA)

    def plan(self,name='media',voice=True,attachments=True):
        return make_replace_plan({'owner':A,'root':str(self.target[0]),'keys':str(self.target[1])},
            {'owner':B,'root':str(self.source[0]),'keys':str(self.source[1])},self.root/name,
            include_voice=voice,include_attachments=attachments)

    def test_mixed_media_encrypted_apply_and_undo_preserve_b_and_other_chats(self):
        with patch.object(ops,'ensure_closed'):
            result=self.plan();d=result['directions']['A']
            self.assertEqual(result['version'],5)
            self.assertEqual((d['image_added'],d['video_added'],d['file_added'],d['voice_added']),(1,1,1,1))
            self.assertFalse(d['client_media_verified']);self.assertFalse(d['phone_verified'])
            self.assertIsNone(result['directions']['B']['plan'])
            recovered=self.root/'media/A-plan/recovered-plain'
            with db(recovered/MEDIA) as c:
                self.assertEqual(c.execute('SELECT svr_id,local_id,voice_data FROM VoiceInfo WHERE chat_name_id=58').fetchall(),[(104,4,AUDIO1)])
                self.assertEqual(c.execute('SELECT svr_id FROM VoiceInfo WHERE chat_name_id=57').fetchall(),[(901,)])
            self.assertEqual(ops.state(self.target[0],True),self.a_before)
            self.assertEqual(list(self.target[0].parent.glob('msg/**/*.*')),[])
            self.assertEqual(sync.apply_sync_plan(self.root/'media')['state'],'applied')
            for kind,name,body in ((3,TOKEN+'.dat',PNG),(43,VTOKEN+'.mp4',VIDEO),(FILE,'测试文档.txt',TEXT)):
                self.assertEqual(att.path_at(self.target[0].parent,att.destination(kind,B,'2023-11',name)).read_bytes(),body)
            self.assertEqual(ops.state(self.source[0],True),self.b_before)
            self.assertEqual(sync.undo_sync_plan(self.root/'media')['state'],'undone')
        self.assertEqual(ops.state(self.target[0],True),self.a_before)
        self.assertEqual(ops.state(self.source[0],True),self.b_before)
        self.assertFalse(att.path_at(self.target[0].parent,att.destination(3,B,'2023-11',TOKEN+'.dat')).exists())

    def test_dat_v2_reencrypted_with_independently_verified_target_sample(self):
        source_key,target_key=b'1'*16,b'2'*16
        header=att.DAT2+struct.pack('<II',32,8)+b'\0'
        source=att.path_at(self.source[0].parent,att.destination(3,A,'2023-11',TOKEN+'.dat'))
        source.write_bytes(att.encode_dat(PNG,header,source_key,7))
        with db(self.b_plain/ops.ALL[1]) as c:c.execute('UPDATE MessageResourceDetail SET size=? WHERE resource_id=8',(source.stat().st_size,))
        self.install(self.source,self.b_plain)
        sample=put(self.target[0].parent,att.destination(3,'wxid_sample','2023-11','c'*32+'.dat'),att.encode_dat(PNG,header,target_key,9))
        def candidates(context):return iter([(source_key,7)] if context.owner==B else [(target_key,9)])
        with patch.object(att.Images,'candidates',candidates),patch.object(ops,'ensure_closed'):
            self.plan()
            sample.unlink()
            with self.assertRaisesRegex(ValueError,'没有可核验'):self.plan('no-target-dat-evidence')
        candidate=self.root/'media/A-plan/attachment-candidates'/att.destination(3,B,'2023-11',TOKEN+'.dat')
        self.assertNotEqual(candidate.read_bytes(),source.read_bytes())
        self.assertEqual(att.decode_dat(candidate.read_bytes(),target_key,9),PNG)
        value=json.loads((self.root/'media/A-plan/plan.json').read_text(encoding='utf-8'))
        self.assertIn(sample.relative_to(self.target[0].parent).as_posix(),value['assets']['originals'])

    def test_missing_audio_or_attachment_stops_before_real_writes(self):
        with db(self.b_plain/MEDIA) as c:c.execute('UPDATE VoiceInfo SET voice_data=NULL')
        self.encrypt_media(self.source,self.b_plain)
        with patch.object(ops,'ensure_closed'),self.assertRaisesRegex(ValueError,'语音音频缺失'):
            self.plan('missing-audio')
        self.assertFalse((self.root/'missing-audio/sync-plan.json').exists())
        image=att.path_at(self.source[0].parent,att.destination(3,A,'2023-11',TOKEN+'.dat'));image.unlink()
        with patch.object(ops,'ensure_closed'),self.assertRaisesRegex(ValueError,'未完整下载'):
            self.plan('missing-image',voice=False)
        self.assertEqual(ops.state(self.target[0],True),self.a_before)

    def test_media_key_and_full_receipt_required(self):
        self.source[1].with_suffix('.capture.json').write_text(json.dumps({'state':'verified','owner':B,
            'database_root':str(self.source[0]),'file_version':'4.2.0.13','verified_required_databases':4}),encoding='utf-8')
        with patch.object(ops,'ensure_closed'),self.assertRaisesRegex(ValueError,'回执'):
            self.plan()
        self.assertFalse((self.root/'media/sync-plan.json').exists())

    def test_changed_source_file_blocks_before_any_a_write(self):
        with patch.object(ops,'ensure_closed'):
            self.plan()
            att.path_at(self.source[0].parent,att.destination(FILE,A,'2023-11','测试文档.txt')).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'附件在预览后改变'):
                sync.apply_sync_plan(self.root/'media')
        self.assertFalse((self.root/'media/sync-started.json').exists())
        self.assertEqual(ops.state(self.target[0],True),self.a_before)

    def test_media_wal_change_blocks_application(self):
        with patch.object(ops,'ensure_closed'):
            self.plan();(self.target[0]/(MEDIA+'-wal')).write_bytes(b'synthetic changed media WAL')
            with self.assertRaisesRegex(ValueError,'已改变'):sync.apply_sync_plan(self.root/'media')
        self.assertFalse((self.root/'media/sync-started.json').exists())

    def test_file_change_after_apply_blocks_undo(self):
        with patch.object(ops,'ensure_closed'):
            self.plan();sync.apply_sync_plan(self.root/'media')
            image=att.path_at(self.target[0].parent,att.destination(3,B,'2023-11',TOKEN+'.dat'));image.write_bytes(b'new local file')
            with self.assertRaisesRegex(ValueError,'拒绝撤回'):sync.undo_sync_plan(self.root/'media')
        self.assertEqual(image.read_bytes(),b'new local file')

    def test_attachment_failure_rolls_back_all_databases_and_files(self):
        with patch.object(ops,'ensure_closed'):
            self.plan();original_replace=os.replace;calls=[]
            def fail(source,dest):
                if '.chat-rescue-stage-' in str(source):
                    calls.append(str(dest))
                    if len(calls)==2:raise OSError('synthetic media failure')
                return original_replace(source,dest)
            with patch.object(os,'replace',side_effect=fail),self.assertRaises(RuntimeError):
                sync.apply_sync_plan(self.root/'media')
        self.assertEqual(len(calls),2)
        self.assertEqual(ops.state(self.target[0],True),self.a_before)
        self.assertEqual(ops.state(self.source[0],True),self.b_before)
        rollback=json.loads((self.root/'media/A-plan/rollback.json').read_text(encoding='utf-8'))
        self.assertEqual(rollback['state'],'rolled_back')

    def test_new_plan_uses_exact_capabilities_and_rejects_b_write(self):
        with patch.object(ops,'ensure_closed'):
            self.plan();path=self.root/'media/sync-plan.json';value=json.loads(path.read_text(encoding='utf-8'))
            value['directions']['B']['plan']=str(self.root/'media/B-plan');path.write_text(json.dumps(value),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'B 必须保持只读'):sync.apply_sync_plan(self.root/'media')
        self.assertEqual(ops.state(self.target[0],True),self.a_before)

    def test_source_and_target_multishard_media_follow_native_witnesses(self):
        from test_routing import add_witnesses,ensure_fts
        rel='message/message_2.db'
        for plain in (self.a_plain,self.b_plain):shutil.copy2(plain/ops.ALL[0],plain/rel)
        with db(self.b_plain/ops.ALL[0]) as c:c.execute('DELETE FROM '+table_for(A)+' WHERE server_id!=101')
        with db(self.b_plain/rel) as c:
            c.execute('DELETE FROM '+table_for(A)+' WHERE server_id=101')
            c.execute('UPDATE Name2Id SET rowid=70 WHERE user_name=?',(A,))
            c.execute('UPDATE Name2Id SET rowid=90 WHERE user_name=?',(B,))
            c.execute('UPDATE '+table_for(A)+' SET local_id=local_id-1')
            c.execute('UPDATE '+table_for(A)+' SET real_sender_id=70,packed_info_data=? WHERE real_sender_id=7',(bytes.fromhex('0846105a'),))
            c.execute('UPDATE '+table_for(A)+' SET real_sender_id=90,packed_info_data=? WHERE real_sender_id=9',(bytes.fromhex('085a1046'),))
        with db(self.b_plain/ops.ALL[1]) as c:c.execute('UPDATE MessageResourceInfo SET message_local_id=message_local_id-1 WHERE message_svr_id>101')
        with db(self.b_plain/MEDIA) as c:c.execute('UPDATE VoiceInfo SET local_id=3 WHERE svr_id=104')
        with db(self.a_plain/rel) as c:
            c.execute('UPDATE Name2Id SET rowid=15 WHERE user_name=?',(A,))
            c.execute('UPDATE Name2Id SET rowid=68 WHERE user_name=?',(B,))
        add_witnesses(self.a_plain,ops.ALL[0],1699999900,1700000000,'old')
        add_witnesses(self.a_plain,rel,1700000001,1700000100,'new')
        ensure_fts(self.b_plain)
        for account,plain in ((self.source,self.b_plain),(self.target,self.a_plain)):
            root,keys=account
            shutil.copy2(root/ops.ALL[0],root/rel)
            value=json.loads(keys.read_text(encoding='utf-8'));value['keys'][rel]=value['keys'][ops.ALL[0]]
            keys.write_text(json.dumps(value),encoding='utf-8')
            self.install(account,plain);self.encrypt_media(account,plain)
            candidate=Path(str(root/rel)+'.test');encrypt_database(plain/rel,candidate,root/rel,keys);os.replace(candidate,root/rel)
            receipt=keys.with_suffix('.capture.json');value=json.loads(receipt.read_text(encoding='utf-8'))
            value.update(verified_required_databases=6,verified_databases=list(ops.inspection_files(root,True)))
            receipt.write_text(json.dumps(value),encoding='utf-8')
        before=ops.state(self.target[0],True);source_before=ops.state(self.source[0],True)
        with patch.object(ops,'ensure_closed'):
            result=self.plan()
            self.assertEqual([item['imported'] for item in result['directions']['A']['target_shard_imports']],[1,3])
            recovered=self.root/'media/A-plan/recovered-plain'
            with db(recovered/rel) as c:
                self.assertEqual(c.execute('SELECT server_id,local_id,real_sender_id FROM '+table_for(B)+' ORDER BY server_id').fetchall(),[(102,1,68),(103,2,15),(104,3,68)])
            with db(recovered/MEDIA) as c:self.assertEqual(c.execute('SELECT local_id,voice_data FROM VoiceInfo WHERE svr_id=104').fetchone(),(3,AUDIO1))
            sync.apply_sync_plan(self.root/'media');sync.undo_sync_plan(self.root/'media')
        self.assertEqual(ops.state(self.target[0],True),before)
        self.assertEqual(ops.state(self.source[0],True),source_before)

    def test_unknown_voice_layout_and_conflicting_target_file_are_refused(self):
        with db(self.b_plain/MEDIA) as c:c.execute('CREATE TABLE UnsupportedMedia(extra BLOB)')
        self.encrypt_media(self.source,self.b_plain)
        with patch.object(ops,'ensure_closed'),self.assertRaisesRegex(ValueError,'未适配表'):
            self.plan('schema')
        put(self.target[0].parent,att.destination(FILE,B,'2023-11','测试文档.txt'),b'new target content')
        with patch.object(ops,'ensure_closed'),self.assertRaisesRegex(ValueError,'collision'):
            self.plan('conflict',voice=False)
        self.assertEqual(ops.state(self.target[0],True),self.a_before)

    def test_voice_and_attachment_switches_work_independently(self):
        with patch.object(ops,'ensure_closed'):
            for voice,attachments in ((True,False),(False,True)):
                name='voice-only' if voice else 'attachments-only'
                self.plan(name,voice,attachments)
                value=json.loads((self.root/name/'A-plan/plan.json').read_text(encoding='utf-8'))
                self.assertEqual(windows_options(value),{'include_voice':voice,'include_attachments':attachments})
                self.assertEqual('assets' in value,attachments)
                self.assertEqual(MEDIA in value['write_set'],voice)
                sync.apply_sync_plan(self.root/name);sync.undo_sync_plan(self.root/name)
        self.assertEqual(ops.state(self.target[0],True),self.a_before)
        self.assertEqual(ops.state(self.source[0],True),self.b_before)

    def test_process_interruption_keeps_coordinated_originals_and_blocks_retry(self):
        with patch.object(ops,'ensure_closed'):self.plan()
        code='''import os,sys
from chat_rescue import operations as ops,sync
ops.ensure_closed=lambda:None
replace=os.replace
def interrupted(source,destination):
    replace(source,destination)
    if '.chat-rescue-stage-' in str(source):os._exit(67)
os.replace=interrupted
sync.apply_sync_plan(sys.argv[1])
'''
        process=subprocess.run([sys.executable,'-X','utf8','-c',code,str(self.root/'media')],capture_output=True)
        self.assertEqual(process.returncode,67,process.stderr.decode('utf-8',errors='replace'))
        directory=self.root/'media/A-plan'
        self.assertTrue((directory/'apply-started.json').exists())
        self.assertFalse((directory/'applied.json').exists())
        for rel,h in self.a_before.items():
            p=directory/'originals'/rel
            self.assertEqual(digest(p) if p.exists() else None,h)
        self.assertEqual(ops.state(self.source[0],True),self.b_before)
        with patch.object(ops,'ensure_closed'),self.assertRaisesRegex(ValueError,'不要重复执行'):
            sync.apply_sync_plan(self.root/'media')


class WindowsCryptoTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows CNG')
    def test_known_aes_vector_padding_and_bad_ciphertext(self):
        # NIST SP 800-38A AES-128 ECB first block, followed by PKCS#7 padding.
        key=bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
        body=bytes.fromhex('6bc1bee22e409f96e93d7e117393172a')
        cipher=att.aes(body,key,True)
        self.assertEqual(cipher[:16].hex(),'3ad77bb40d7a3660a89ecaf32466ef97')
        self.assertEqual(att.aes(cipher,key,False),body)
        self.assertEqual(att.aes(att.aes(b'',key,True),key,False),b'')
        with self.assertRaises(ValueError):att.aes(cipher[:-1],key,False)
        with self.assertRaises(ValueError):att.aes(cipher,b'3'*16,False)

    def test_version5_exact_negotiation_and_windows_unsafe_names(self):
        self.assertEqual(windows_options({'version':5,'capabilities':windows_capabilities(True,True)}),{'include_voice':True,'include_attachments':True})
        for caps in ([],['mac-attachments-v1'],windows_capabilities(True,False)+['unknown']):
            with self.assertRaises(ValueError):windows_options({'version':5,'capabilities':caps})
        for name in ('CON','con.txt','NUL.zip','COM1.doc','file.','file ','a:b','a?b'):
            with self.assertRaises(ValueError):att.filename(name)


class WindowsMediaControlTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows CNG doctor self-test')
    def test_doctor_checks_windows_cng_without_lldb_or_account_access(self):
        from chat_rescue import __main__ as cli
        with patch.object(cli,'supported_host'),patch.object(cli,'library'),patch.object(cli,'run') as run:
            result=cli.doctor()
            self.assertTrue(result['attachment_crypto_ready'])
            self.assertNotIn('apple_lldb',result['checks'])
            self.assertFalse(result['key_capture_available'])
            run.assert_not_called()
            with patch.object(att,'aes',side_effect=ValueError('synthetic CNG failure')):
                self.assertFalse(cli.doctor()['attachment_crypto_ready'])

    def test_orphan_media_sidecar_and_cross_platform_plans_are_rejected(self):
        from chat_rescue.voice import single_media, CAPABILITY
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);put(root,MEDIA,b'synthetic file')
            single_media(root);put(root,'message/media_1.db-wal',b'orphan')
            with self.assertRaisesRegex(ValueError,'sidecar'):single_media(root)
        with patch('platform.system',return_value='Windows'):
            with self.assertRaisesRegex(ValueError,'不处理 Mac'):ops.plan_sets({'version':2,'capabilities':[CAPABILITY]})
        with patch('platform.system',return_value='Darwin'):
            with self.assertRaisesRegex(ValueError,'平台不匹配'):sync.sync_options({'version':5})

    def test_partial_scan_cannot_finish_before_media_key(self):
        import test_windows_capture
        from chat_rescue import windows_capture as capture
        memory=test_windows_capture.Memory()
        key=os.urandom(32)
        memory.pages[MEDIA]=test_windows_capture.page_for(key,os.urandom(16))
        with self.assertRaises(RuntimeError):capture.scan(memory,memory.pages,time.monotonic()+10)

    def test_capture_requires_all_media_integrity_checks_before_save(self):
        import test_windows_capture
        from chat_rescue import windows_capture as capture
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);root=directory/'synthetic_owner/db_storage'
            memory=test_windows_capture.Memory();memory.pages[MEDIA]=test_windows_capture.page_for(os.urandom(32),os.urandom(16))
            for rel,body in memory.pages.items():put(root,rel,body)
            exe=directory/'Weixin.exe';exe.write_bytes(b'synthetic binary')
            metadata={'executable':str(exe),'sha256':digest(exe),'file_version':'4.2.0.13'}
            keys={**memory.keys,MEDIA:'ab'*32}
            with patch.object(capture,'app_info',return_value=metadata),patch.object(capture,'Process') as process, \
                    patch.object(capture,'scan',return_value=keys) as scan,patch.object(capture,'require_private'), \
                    patch('chat_rescue.vendor.sqlcipher_probe.DB') as database:
                database.return_value.__enter__.return_value.query.return_value=[{'integrity_check':'ok'}]
                result=capture.capture(123,exe,root,'synthetic_owner',directory/'keys.json',include_voice=True)
                self.assertEqual(result['verified_required_databases'],5)
                self.assertEqual(set(scan.call_args.args[1]),set(memory.pages))
                self.assertEqual(database.call_count,5);process.return_value.close.assert_called_once()
            self.assertIn(MEDIA,json.loads((directory/'keys.capture.json').read_text(encoding='utf-8'))['verified_databases'])
            with patch.object(capture,'app_info',return_value=metadata),patch.object(capture,'Process'), \
                    patch.object(capture,'scan',return_value=memory.keys),patch.object(capture,'require_private'):
                with self.assertRaises(RuntimeError):capture.capture(123,exe,root,'synthetic_owner',directory/'partial.json',include_voice=True)
            self.assertFalse((directory/'partial.json').exists())

    def test_desktop_forwards_explicit_flags_and_media_key_verification(self):
        from chat_rescue import windows_backend as backend
        selected={'owner':'synthetic_owner','root':'synthetic-root','keys':'synthetic.json'}
        with patch.object(backend,'account',return_value=selected),patch.object(backend,'ensure_closed'), \
                patch.object(backend,'main_processes',return_value=[]),patch.object(backend,'require_private'), \
                patch.object(backend.cli,'verify_keys') as verify,patch.object(backend,'account_nickname',return_value='Synthetic'):
            backend.dispatch({'action':'capture','account':{},'identity_confirmed':True,'executable':'synthetic.exe','include_voice':True})
            verify.assert_called_once_with('synthetic-root','synthetic.json',include_voice=True)
        with patch.object(backend,'account',return_value=selected),patch.object(backend,'ensure_closed'), \
                patch.object(backend,'require_private'),patch.object(backend,'job',return_value=Path('synthetic-job')), \
                patch.object(backend,'make_replace_plan') as plan,patch.object(backend,'plan_view',return_value={}):
            backend.dispatch({'action':'plan','a':{},'b':{},'identity_confirmed':True,'source_history_confirmed':True,
                'include_voice':True,'include_attachments':True})
            self.assertEqual(plan.call_args.kwargs,{'include_voice':True,'include_attachments':True})
