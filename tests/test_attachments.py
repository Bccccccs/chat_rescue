"""Synthetic media containers validate copying/transactions, not WeChat display."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import zlib
from unittest.mock import patch
from test_workflow import A,B,fixture,db,FIELDS
from chat_rescue import attachments as att, operations as ops, sync
from chat_rescue.workflow import save_chat,prepare_plain_restore,table_for,connect,private_json,digest
from chat_rescue.cipher import encrypt_database,export_database
from chat_rescue.vendor.sqlcipher_probe import DB,library

FILE=(6<<32)|49
TOKEN='a'*32
VTOKEN='b'*32
TEXT=b'Synthetic document. Never opened or executed.\n'
VIDEO=struct.pack('>I',24)+b'ftypisom'+b'\x00\x00\x02\x00isomiso2'+struct.pack('>I',12)+b'mdat'+b'test'

def png():
    def chunk(name,data):return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\x20\x40\x80'))+chunk(b'IEND',b'')
PNG=png()


def put(root,rel,body):
    p=Path(root)/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(body);return p


def attachment_fixture(plain,assets,source):
    fixture(plain,source);Path(assets).mkdir(parents=True,exist_ok=True)
    if not source:return
    t=table_for(A)
    with db(plain/ops.RELS[0]) as c:
        c.execute('UPDATE '+t+' SET local_type=3,status=3,download_status=1,message_content=? WHERE local_id=1',(b'<msg><img/></msg>',))
        c.execute('UPDATE '+t+' SET local_type=43,status=3,download_status=1,message_content=? WHERE local_id=2',(b'<msg><videomsg/></msg>',))
        row=list(c.execute('SELECT * FROM '+t+' WHERE local_id=1').fetchone());row[0]=3;row[1]=103;row[2]=FILE;row[3]+=2000;row[5]+=2
        row[12]=('<msg><appmsg><type>6</type><title>测试文档.txt</title><appattach><totallen>'+str(len(TEXT))+'</totallen><md5>'+hashlib.md5(TEXT).hexdigest()+'</md5></appattach></appmsg></msg>').encode()
        c.execute('INSERT INTO '+t+' VALUES('+','.join('?' for _ in row)+')',row)
    with db(plain/ops.RELS[1]) as c:
        c.execute('DELETE FROM MessageResourceDetail');c.execute('DELETE FROM MessageResourceInfo')
        c.execute('INSERT INTO SenderName2Id(rowid,user_name,update_time) VALUES(14,?,0)',(B,))
        for mid,lid,sid,kind,sender,token,size,dtype in [(8,1,101,3,13,TOKEN,len(PNG),131073),(9,2,102,43,14,VTOKEN,len(VIDEO),196610),(10,3,103,FILE,13,None,len(TEXT),3)]:
            packed=b'\x0a\x20'+token.encode() if token else b''
            c.execute('INSERT INTO MessageResourceInfo VALUES(?,11,?,?,?,?,?,6,?)',(mid,sender,kind,1700000000+lid-1,lid,sid,packed))
            c.execute("INSERT INTO MessageResourceDetail VALUES(?,?,?, ?,1700000000,0,1,'0',NULL)",(mid,mid,dtype,size))
    put(assets,att.destination(3,A,'2023-11',TOKEN+'.dat'),PNG)
    put(assets,att.destination(43,A,'2023-11',VTOKEN+'.mp4'),VIDEO)
    put(assets,att.destination(FILE,A,'2023-11','测试文档.txt'),TEXT)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'source';self.target=self.root/'target';self.bundle=self.root/'bundle'
        self.bassets=self.root/'Documents/xwechat_files'/B;self.aassets=self.root/'Documents/xwechat_files'/A
        attachment_fixture(self.source,self.bassets,True);attachment_fixture(self.target,self.aassets,False)

    def save(self,**kw):return save_chat(self.source,B,A,self.bundle,include_attachments=True,asset_root=self.bassets,**kw)
    def restore(self,target=None,name='out'):return prepare_plain_restore(self.bundle,target or self.target,A,self.root/name,include_attachments=True,target_asset_root=self.aassets)
    def image(self,peer=A,assets=None):return att.path_at(assets or self.bassets,att.destination(3,peer,'2023-11',TOKEN+'.dat'))

    def test_images_video_files_original_bytes_and_peer_directory(self):
        self.save();r=self.restore()
        self.assertEqual((r['image_added'],r['video_added'],r['file_added']),(1,1,1))
        self.assertEqual(r['added'],3)
        candidate=self.root/'out/attachment-candidates'
        self.assertEqual(att.path_at(candidate,att.destination(3,B,'2023-11',TOKEN+'.dat')).read_bytes(),PNG)
        self.assertEqual(att.path_at(candidate,att.destination(43,B,'2023-11',VTOKEN+'.mp4')).read_bytes(),VIDEO)
        self.assertEqual(att.path_at(candidate,att.destination(FILE,B,'2023-11','测试文档.txt')).read_bytes(),TEXT)
        self.assertEqual(list(self.aassets.rglob('*.dat')),[])  # No live writes during planning.
        with connect(self.root/'out'/ops.RELS[0]) as c:
            self.assertEqual([tuple(x) for x in c.execute('SELECT server_id,real_sender_id FROM '+table_for(B)+' ORDER BY server_id')],[(101,4),(102,302),(103,4)])

    def test_existing_messages_missing_files_only_repair_and_idempotence(self):
        self.save();self.restore()
        before={r:digest(self.root/'out'/r) for r in ops.ALL}
        r=self.restore(self.root/'out','repair')
        self.assertEqual((r['added'],r['image_repaired'],r['video_repaired'],r['file_repaired']),(0,1,1,1))
        self.assertEqual(before,{r:digest(self.root/'repair'/r) for r in ops.ALL})
        shutil.copytree(self.root/'repair/attachment-candidates/msg',self.aassets/'msg')
        r=self.restore(self.root/'repair','again')
        self.assertEqual(r['assets']['candidates'],{});self.assertEqual(r['changed_databases'],[])
        self.assertEqual(r['attachments_skipped'],3)

    def test_missing_source_rejected_or_complete_target_used(self):
        self.save();self.restore();shutil.copytree(self.root/'out/attachment-candidates/msg',self.aassets/'msg')
        shutil.rmtree(self.bundle);self.image().unlink()
        with self.assertRaises(ValueError):self.save()
        if self.bundle.exists():shutil.rmtree(self.bundle)
        self.save(allow_missing_attachments=True)
        r=self.restore(self.root/'out','keep');self.assertEqual(r['attachments_skipped'],3)
        self.image(B,self.aassets).unlink()
        with self.assertRaises(ValueError):self.restore(self.root/'out','both-missing')

    def test_source_file_size_digest_name_and_thumbnail_only_are_checked(self):
        path=att.path_at(self.bassets,att.destination(FILE,A,'2023-11','测试文档.txt'));path.write_bytes(b'wrong')
        with self.assertRaises(ValueError):self.save()
        path.write_bytes(TEXT)
        if self.bundle.exists():shutil.rmtree(self.bundle)
        self.image().rename(self.image().with_name(TOKEN+'_t.dat'))
        with self.assertRaisesRegex(ValueError,'缩略图'):self.save()

    def test_conflicting_target_nonempty_file_rejected(self):
        self.save();put(self.aassets,att.destination(FILE,B,'2023-11','测试文档.txt'),b'newer different document')
        with self.assertRaisesRegex(ValueError,'collision'):self.restore()

    def test_tampered_paths_payloads_and_legacy_rejection(self):
        self.save()
        with self.assertRaises(ValueError):prepare_plain_restore(self.bundle,self.target,A,self.root/'old')
        p=self.bundle/'conversation.json';data=json.loads(p.read_text());data['attachments'][0]['files'][0]['payload']='../outside'
        p.write_text(json.dumps(data));m=self.bundle/'manifest.json';v=json.loads(m.read_text());v['sha256']=digest(p);m.write_text(json.dumps(v))
        with self.assertRaises(ValueError):self.restore()

    def test_symlink_or_unsafe_account_path_rejected(self):
        for name in ('../secret','/tmp/a','msg/file/2023-11/../a','msg/other/2023-11/file','msg/file/2023-13/file','msg/file/2023-11/a\\b'):
            with self.subTest(name=name),self.assertRaises(ValueError):att.validate_path(name)
        self.save();dest=self.aassets/'msg';elsewhere=self.root/'elsewhere';elsewhere.mkdir()
        try:dest.symlink_to(elsewhere,target_is_directory=True)
        except OSError as e:
            if getattr(e,'winerror',None)==1314:self.skipTest('Windows symlink privilege unavailable')
            raise
        with self.assertRaises(ValueError):self.restore()

    def test_source_resource_identity_and_format_checked(self):
        self.image().write_bytes(b'not-an-image')
        with db(self.source/ops.RELS[1]) as c:c.execute('UPDATE MessageResourceDetail SET size=? WHERE type=131073',(len(b'not-an-image'),))
        with self.assertRaises(ValueError):self.save()


@unittest.skipIf(os.name=='nt','DAT crypto uses Mac bundled OpenSSL')
class ImageCryptoTests(unittest.TestCase):
    setUp=AttachmentTests.setUp
    save=AttachmentTests.save
    restore=AttachmentTests.restore
    image=AttachmentTests.image

    def set_dat(self,version=att.DAT2):
        code=12345;folder=self.root/'Documents/app_data/net/kvcomm';folder.mkdir(parents=True);(folder/f'key_{code}_synthetic.statistic').touch()
        header=version+struct.pack('<II',32,8)+b'\0'
        bkey=hashlib.md5((str(code)+B).encode()).hexdigest()[:16].encode();akey=hashlib.md5((str(code)+A).encode()).hexdigest()[:16].encode()
        if version==att.DAT1:bkey=akey=b'cfcd208495d565ef'
        encrypted=att.encode_dat(PNG,header,bkey,code&255);self.image().write_bytes(encrypted)
        proof=att.destination(3,'wxid_other_demo','2023-11','c'*32+'.dat')
        put(self.aassets,proof,att.encode_dat(PNG,header,akey,code&255))
        with db(self.source/ops.RELS[1]) as c:c.execute('UPDATE MessageResourceDetail SET size=? WHERE type=131073',(len(encrypted),))
        return encrypted,proof,header,akey,code&255

    def test_v2_reencrypt_for_different_account_without_changing_image_bytes(self):
        encrypted,proof,header,key,xor=self.set_dat();self.save();r=self.restore()
        p=att.path_at(self.root/'out/attachment-candidates',att.destination(3,B,'2023-11',TOKEN+'.dat'))
        self.assertNotEqual(p.read_bytes(),encrypted)
        self.assertEqual(att.decode_dat(p.read_bytes(),key,xor),PNG)
        self.assertIn(proof,r['assets']['originals'])
        data=(self.bundle/'conversation.json').read_text()
        self.assertNotIn(key.decode(),data)

    def test_v2_without_target_proof_stops_instead_of_copying_foreign_ciphertext(self):
        encrypted,proof,header,key,xor=self.set_dat();att.path_at(self.aassets,proof).unlink()
        self.save()
        with self.assertRaisesRegex(ValueError,'样本'):self.restore()

    def test_v1_and_old_xor_roundtrip_original_payload(self):
        encrypted,proof,header,key,xor=self.set_dat(att.DAT1);self.save();self.restore()
        path=att.path_at(self.root/'out/attachment-candidates',att.destination(3,B,'2023-11',TOKEN+'.dat'))
        context=att.Images(self.aassets,A);self.assertEqual(context.decode(path.read_bytes())[0],PNG)
        shutil.rmtree(self.bundle);self.image().write_bytes(bytes(x^0x5a for x in PNG))
        with db(self.source/ops.RELS[1]) as c:c.execute('UPDATE MessageResourceDetail SET size=? WHERE type=131073',(len(PNG),))
        att.path_at(self.aassets,proof).write_bytes(bytes(x^0x6b for x in PNG))
        self.save();self.restore(name='xor')
        path=att.path_at(self.root/'xor/attachment-candidates',att.destination(3,B,'2023-11',TOKEN+'.dat'))
        self.assertEqual(path.read_bytes(),bytes(x^0x6b for x in PNG))


@unittest.skipIf(os.name=='nt','Mac attachment integration')
class EncryptedAttachmentTests(unittest.TestCase):
    def setUp(self):
        try:library()
        except (RuntimeError,OSError):self.skipTest('SQLCipher required')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        gate=patch.object(ops,'ensure_closed');gate.start();self.addCleanup(gate.stop)
        gate=patch.object(ops,'supported_host');gate.start();self.addCleanup(gate.stop)
        self.a=self.encrypted(A,False);self.b=self.encrypted(B,True)

    def encrypted(self,owner,source):
        plain=self.root/(owner+'-plain');assets=self.root/'Documents/xwechat_files'/owner
        attachment_fixture(plain,assets,source);live=assets/'db_storage';keys=self.root/(owner+'-keys.json');manifest={}
        for rel in ops.ALL:
            p=live/rel;p.parent.mkdir(parents=True,exist_ok=True);key=os.urandom(32).hex();manifest[rel]={'enc_key':key}
            with DB(p,key,fixture_write=True) as c:c.query('CREATE TABLE template(id INTEGER PRIMARY KEY);')
        private_json(keys,{'keys':manifest})
        for rel in ops.ALL:
            candidate=Path(str(live/rel)+'.candidate');encrypt_database(plain/rel,candidate,live/rel,keys);os.replace(candidate,live/rel)
        return {'owner':owner,'root':str(live),'keys':str(keys)}

    def plan(self,name='sync'):return sync.make_sync_plan(self.a,self.b,self.root/name,include_attachments=True)

    def test_encrypted_apply_complete_undo_and_file_only_repair(self):
        before=ops.state(self.a['root']);r=self.plan();self.assertEqual(r['directions']['A']['image_added'],1)
        manifest=json.loads((self.root/'sync/sync-plan.json').read_text());self.assertEqual(manifest['version'],3)
        sync.apply_sync_plan(self.root/'sync')
        root=Path(self.a['root']).parent;image=att.path_at(root,att.destination(3,B,'2023-11',TOKEN+'.dat'))
        self.assertEqual(image.read_bytes(),PNG)
        r=self.plan('unchanged');self.assertTrue(all(x['state']=='unchanged' for x in r['directions'].values()))
        sync.undo_sync_plan(self.root/'sync');self.assertEqual(ops.state(self.a['root']),before);self.assertFalse(image.exists())
        self.plan('again');sync.apply_sync_plan(self.root/'again')
        image.unlink();r=self.plan('repair');self.assertEqual(r['directions']['A']['added'],0);self.assertEqual(r['directions']['A']['image_repaired'],1)
        p=json.loads((self.root/'repair/A-plan/plan.json').read_text());self.assertEqual(p['write_set'],[])
        sync.apply_sync_plan(self.root/'repair');self.assertEqual(image.read_bytes(),PNG)
        sync.undo_sync_plan(self.root/'repair');self.assertFalse(image.exists())

    def test_attachment_changes_candidate_tamper_and_parent_links_block_both(self):
        self.plan();p=self.root/'sync/A-plan/attachment-candidates'/att.destination(3,B,'2023-11',TOKEN+'.dat')
        p.write_bytes(b'tamper');before=ops.state(self.a['root'])
        with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')
        self.assertEqual(ops.state(self.a['root']),before);self.assertFalse((self.root/'sync/sync-started.json').exists())

    def test_failure_during_second_attachment_restores_databases_and_files(self):
        self.plan();before=ops.state(self.a['root']);real=os.replace;count=0
        def fail(a,b):
            nonlocal count
            if Path(a).name.startswith('.chat-rescue-stage-'):
                count+=1
                if count==2:raise OSError('synthetic second attachment failure')
            return real(a,b)
        with patch('chat_rescue.operations.os.replace',side_effect=fail):
            with self.assertRaises(RuntimeError):sync.apply_sync_plan(self.root/'sync')
        self.assertEqual(ops.state(self.a['root']),before)
        self.assertEqual(list(Path(self.a['root']).parent.glob('msg/**/*.dat')),[])
        self.assertEqual(json.loads((self.root/'sync/A-plan/rollback.json').read_text())['state'],'rolled_back')

    def test_live_attachment_change_and_new_file_appearance_block_apply(self):
        self.plan();source=att.path_at(Path(self.b['root']).parent,att.destination(3,A,'2023-11',TOKEN+'.dat'));source.write_bytes(source.read_bytes()+b'new')
        with self.assertRaises(ValueError):sync.apply_sync_plan(self.root/'sync')
        self.assertFalse((self.root/'sync/sync-started.json').exists())

    def test_attachment_change_after_apply_blocks_undo(self):
        self.plan();sync.apply_sync_plan(self.root/'sync')
        image=att.path_at(Path(self.a['root']).parent,att.destination(3,B,'2023-11',TOKEN+'.dat'));image.write_bytes(image.read_bytes()+b'user edit')
        before=ops.state(self.a['root'])
        with self.assertRaises(ValueError):sync.undo_sync_plan(self.root/'sync')
        self.assertEqual(ops.state(self.a['root']),before)
        self.assertFalse((self.root/'sync/undo-started.json').exists())

@unittest.skipIf(os.name=='nt','Mac attachment lifecycle integration')
class AttachmentLifecycleTests(unittest.TestCase):
    setUp=EncryptedAttachmentTests.setUp
    encrypted=EncryptedAttachmentTests.encrypted
    plan=EncryptedAttachmentTests.plan

    def test_real_process_interruption_after_attachment_preserves_all_originals(self):
        import subprocess,sys
        self.plan();directory=self.root/'sync/A-plan'
        script='''import os,sys
from pathlib import Path
from unittest.mock import patch
from chat_rescue.operations import apply_plan
real=os.replace
def replace(a,b):
    real(a,b)
    if Path(a).name.startswith('.chat-rescue-stage-'):os._exit(61)
with patch('chat_rescue.operations.os.replace',side_effect=replace),patch('chat_rescue.operations.ensure_closed'),patch('chat_rescue.operations.supported_host'):
    apply_plan(sys.argv[1])
'''
        result=subprocess.run([sys.executable,'-c',script,str(directory)],capture_output=True,text=True)
        self.assertEqual(result.returncode,61,result.stderr)
        self.assertTrue((directory/'apply-started.json').exists());self.assertFalse((directory/'applied.json').exists())
        journal=[json.loads(x) for x in (directory/'apply-journal.jsonl').read_text().splitlines()]
        self.assertIn('attachment',journal[-1]);self.assertEqual(journal[-1]['state'],'replace_intent')
        for rel in ops.ALL:self.assertTrue((directory/'originals'/rel).exists())
        with self.assertRaises(ValueError):ops.apply_plan(directory)

    def test_undo_failure_repairs_files_and_databases_and_records_failure(self):
        self.plan();sync.apply_sync_plan(self.root/'sync');before=ops.state(self.a['root'])
        directory=Path(self.a['root']).parent
        media={p.relative_to(directory).as_posix():digest(p) for p in directory.glob('msg/**/*') if p.is_file()}
        real=Path.unlink;count=0
        def fail(path,*args,**kw):
            nonlocal count
            if '/msg/' in str(path) and not path.name.startswith('.chat-rescue'):
                count+=1
                if count==2:raise OSError('synthetic undo file deletion failure')
            return real(path,*args,**kw)
        with patch.object(Path,'unlink',new=fail):
            with self.assertRaises(OSError):sync.undo_sync_plan(self.root/'sync')
        self.assertEqual(ops.state(self.a['root']),before)
        self.assertEqual({p.relative_to(directory).as_posix():digest(p) for p in directory.glob('msg/**/*') if p.is_file()},media)
        receipts=list((self.root/'sync').glob('before-undo-*/failure.json'))
        self.assertEqual(json.loads(receipts[0].read_text())['state'],'repaired')

    def test_empty_existing_file_is_preserved_by_undo(self):
        root=Path(self.a['root']).parent;relative=att.destination(3,B,'2023-11',TOKEN+'.dat')
        put(root,relative,b'');self.plan();sync.apply_sync_plan(self.root/'sync');self.assertEqual(att.path_at(root,relative).read_bytes(),PNG)
        sync.undo_sync_plan(self.root/'sync');self.assertEqual(att.path_at(root,relative).read_bytes(),b'')

    def test_resource_only_repair_when_payload_already_present(self):
        self.plan();sync.apply_sync_plan(self.root/'sync')
        plain=self.root/'damaged';ops.export_root(self.a['root'],self.a['keys'],plain,include_attachments=True)
        with db(plain/ops.RELS[1]) as c:c.execute('UPDATE MessageResourceDetail SET status=0,size=0 WHERE type=131073')
        rel=ops.RELS[1];live=Path(self.a['root'])/rel;candidate=Path(str(live)+'.test')
        encrypt_database(plain/rel,candidate,live,self.a['keys']);os.replace(candidate,live)
        r=self.plan('repair');direction=r['directions']['A']
        self.assertEqual(direction['added'],0);self.assertEqual(direction['attachment_resources_repaired'],1)
        p=json.loads((self.root/'repair/A-plan/plan.json').read_text());self.assertEqual(p['write_set'],[ops.RELS[1]])
        self.assertEqual(p['assets']['candidates'],{})
        sync.apply_sync_plan(self.root/'repair')

    def test_voice_and_attachments_use_one_coordinated_plan(self):
        from test_voice import AUDIO1
        for account in (self.a,self.b):
            owner=account['owner'];plain=self.root/(owner+'-plain')
            with db(plain/'message/media_0.db') as c:
                c.executescript("CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY);CREATE TABLE VoiceInfo(chat_name_id INTEGER,create_time INTEGER,local_id INTEGER,svr_id INTEGER,voice_data BLOB,data_index TEXT DEFAULT '0');CREATE UNIQUE INDEX VoiceInfo_UNIQUE_INDEX ON VoiceInfo(chat_name_id,create_time,local_id,data_index);")
                if owner==B:
                    c.execute('INSERT INTO Name2Id(rowid,user_name) VALUES(57,?)',(A,))
                    c.execute("INSERT INTO VoiceInfo VALUES(57,1700000003,4,104,?,'0')",(AUDIO1,))
            if owner==B:
                with db(plain/ops.RELS[0]) as c:
                    row=list(c.execute('SELECT * FROM '+table_for(A)+' WHERE local_id=1').fetchone());row[0]=4;row[1]=104;row[2]=34;row[3]+=3000;row[5]+=3;row[12]=b'synthetic voice metadata'
                    c.execute('INSERT INTO '+table_for(A)+' VALUES('+','.join('?' for _ in row)+')',row)
                live=Path(account['root'])/ops.RELS[0];candidate=Path(str(live)+'.test');encrypt_database(plain/ops.RELS[0],candidate,live,account['keys']);os.replace(candidate,live)
            keys=Path(account['keys']);manifest=json.loads(keys.read_text());key=os.urandom(32).hex();manifest['keys']['message/media_0.db']={'enc_key':key};keys.write_text(json.dumps(manifest))
            live=Path(account['root'])/'message/media_0.db'
            with DB(live,key,fixture_write=True) as c:c.query('CREATE TABLE template(id INTEGER PRIMARY KEY);')
            candidate=Path(str(live)+'.test');encrypt_database(plain/'message/media_0.db',candidate,live,keys);os.replace(candidate,live)
        result=sync.make_sync_plan(self.a,self.b,self.root/'mixed',include_voice=True,include_attachments=True)
        d=result['directions']['A'];self.assertEqual((d['added'],d['voice_added'],d['image_added'],d['video_added'],d['file_added']),(4,1,1,1,1))
        sync.apply_sync_plan(self.root/'mixed');sync.undo_sync_plan(self.root/'mixed')

    def test_cli_export_save_plan_apply_and_resume(self):
        import io,contextlib,sys
        from chat_rescue import __main__ as cli,desktop_backend as backend
        def invoke(*args):
            out=io.StringIO()
            with patch.object(sys,'argv',['chat-rescue',*map(str,args)]),patch.object(cli,'ensure_closed'),contextlib.redirect_stdout(out):
                rc=cli.main()
            self.assertEqual(rc,0);return json.loads(out.getvalue())
        invoke('export-db','--db-root',self.b['root'],'--keys',self.b['keys'],'--output',self.root/'export','--include-attachments')
        invoke('save-chat','--plain-root',self.root/'export','--owner',B,'--peer',A,'--output',self.root/'bundle','--include-attachments')
        r=invoke('plan-restore','--source',self.root/'bundle','--target-db-root',self.a['root'],'--target-keys',self.a['keys'],'--target-owner',A,'--output',self.root/'single','--include-attachments')
        self.assertEqual(r['image_added'],1);self.assertEqual(invoke('apply','--plan',self.root/'single')['state'],'applied')
        self.plan();r=backend.dispatch({'action':'resume','plan':str(self.root/'sync')});self.assertEqual(r['state'],'ready')

class AttachmentExtraTests(unittest.TestCase):
    setUp=AttachmentTests.setUp
    save=AttachmentTests.save
    restore=AttachmentTests.restore
    image=AttachmentTests.image

    def test_shared_image_cache_path_is_deduplicated_across_messages(self):
        with db(self.source/ops.RELS[0]) as c:
            t=table_for(A);row=list(c.execute('SELECT * FROM '+t+' WHERE local_id=1').fetchone());row[0]=4;row[1]=104;row[3]+=3000;row[5]+=3
            c.execute('INSERT INTO '+t+' VALUES('+','.join('?' for _ in row)+')',row)
        with db(self.source/ops.RELS[1]) as c:
            c.execute('INSERT INTO MessageResourceInfo SELECT 20,chat_id,sender_id,message_local_type,message_create_time+3,4,104,message_origin_source,packed_info FROM MessageResourceInfo WHERE message_id=8')
            c.execute('INSERT INTO MessageResourceDetail SELECT 20,20,type,size,create_time,access_time,status,data_index,packed_info FROM MessageResourceDetail WHERE resource_id=8')
        self.save();r=self.restore()
        self.assertEqual(r['added'],4);self.assertEqual(len(r['assets']['candidates']),3)

    def test_compressed_xml_and_entity_rejection(self):
        from compression import zstd
        with connect(self.source/ops.RELS[0]) as c:row=dict(c.execute('SELECT * FROM '+table_for(A)+' WHERE local_id=3').fetchone())
        row['message_content']=zstd.compress(row['message_content'])
        self.assertEqual(att.xml_file(row)[0],'测试文档.txt')
        row['message_content']=b'<!DOCTYPE x [<!ENTITY y "bad">]><msg/>'
        with self.assertRaises(ValueError):att.xml_file(row)

    def test_asset_payload_hash_and_wrong_account_destination_are_rejected(self):
        self.save();payload=self.bundle/'attachments/000001.bin';payload.write_bytes(payload.read_bytes()+b'changed')
        with self.assertRaises(ValueError):self.restore()
        plan={'target_owner':A,'report':{'attachment_peer':B},'assets':{'originals':{},'candidates':{}}}
        wrong=att.destination(3,'wxid_foreign_demo','2023-11',TOKEN+'.dat')
        plan['assets']['originals'][wrong]=None;plan['assets']['candidates'][wrong]='0'*64
        with self.assertRaises(ValueError):att.validate_plan(plan)


@unittest.skipIf(os.name=='nt','Mac complementary attachments')
class ComplementaryAttachmentTests(unittest.TestCase):
    setUp=EncryptedAttachmentTests.setUp
    encrypted=EncryptedAttachmentTests.encrypted
    plan=EncryptedAttachmentTests.plan

    def test_each_account_recovers_different_missing_files_without_new_messages(self):
        self.plan();sync.apply_sync_plan(self.root/'sync')
        aroot=Path(self.a['root']).parent;broot=Path(self.b['root']).parent
        att.path_at(aroot,att.destination(3,B,'2023-11',TOKEN+'.dat')).unlink()
        att.path_at(broot,att.destination(43,A,'2023-11',VTOKEN+'.mp4')).unlink()
        before={s:ops.state(a['root']) for s,a in [('A',self.a),('B',self.b)]}
        r=self.plan('complement')
        self.assertEqual(r['directions']['A']['image_repaired'],1)
        self.assertEqual(r['directions']['B']['video_repaired'],1)
        for slot in ('A','B'):
            p=json.loads((self.root/('complement/'+slot+'-plan/plan.json')).read_text())
            self.assertEqual(p['write_set'],[])
        sync.apply_sync_plan(self.root/'complement')
        for slot,a in [('A',self.a),('B',self.b)]:self.assertEqual(ops.state(a['root']),before[slot])
