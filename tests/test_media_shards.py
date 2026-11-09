"""Synthetic media shards and exact routing; no client playback claims."""
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from test_workflow import A, B, db
from test_voice import voice_fixture, AUDIO1, AUDIO2
from test_attachments import PNG, put
from chat_rescue import voice, attachments as att, operations as ops
from chat_rescue.media import check_voice_layout, windows_capabilities, windows_options
from chat_rescue.shards import receipt_covers


@unittest.skipUnless(os.name=='nt', 'Windows media shards')
class MediaShardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'plain';voice_fixture(self.root,True)
        self.extra='message/media_7.db'
        shutil.copy2(self.root/voice.MEDIA,self.root/self.extra)
        with db(self.root/voice.MEDIA) as c:c.execute('DELETE FROM VoiceInfo WHERE svr_id=102')
        with db(self.root/self.extra) as c:
            c.execute('DELETE FROM VoiceInfo WHERE svr_id=101')
            c.execute('UPDATE Name2Id SET rowid=900')
            c.execute('UPDATE VoiceInfo SET chat_name_id=900')
        self.messages=[{'fields':dict(local_type=34,status=3,upload_status=0,download_status=1,
            server_id=101+i,local_id=1+i,create_time=1700000000+i)} for i in range(2)]

    def test_inventory_keys_collect_and_duplicate_refusal(self):
        self.assertEqual(voice.single_media(self.root),(voice.MEDIA,self.extra))
        check_voice_layout(self.root)
        files=ops.inspection_files(self.root,True)
        receipt=dict(verified_required_databases=len(files),verified_databases=list(files))
        self.assertTrue(receipt_covers(self.root,receipt,True))
        receipt['verified_databases'].remove(self.extra)
        self.assertFalse(receipt_covers(self.root,receipt,True))
        out=self.root/'bundle';out.mkdir()
        entries=voice.collect(self.root,A,self.messages,out)
        self.assertEqual([(out/e['audio']['path']).read_bytes() for e in entries],[AUDIO1,AUDIO2])
        with db(self.root/self.extra) as c:
            c.execute("INSERT INTO VoiceInfo VALUES(900,1700000000,1,101,?,'0')",(AUDIO1,))
        with self.assertRaisesRegex(ValueError,'跨媒体分片'):voice.collect(self.root,A,self.messages,self.root/'dup')
        with self.assertRaisesRegex(ValueError,'跨媒体分片'):voice.target_routing(self.root,A)

    def test_windows_nonzero_media_inventory_and_receipt_are_complete(self):
        (self.root/voice.MEDIA).rename(self.root/'message/media_3.db')
        self.assertEqual(voice.single_media(self.root),('message/media_3.db',self.extra))
        rels=ops.inspection_files(self.root,True)
        receipt={'verified_required_databases':len(rels),'verified_databases':list(rels)}
        self.assertTrue(receipt_covers(self.root,receipt))
        check_voice_layout(self.root)
        with patch('platform.system',return_value='Darwin'),self.assertRaises(ValueError):
            voice.single_media(self.root)

    def test_official_wcdb_material_backups_are_not_media_database_sidecars(self):
        for kind in ('first','incremental','last'):
            (self.root/'message'/('media_0.db-'+kind+'.material')).write_bytes(b'synthetic metadata')
        self.assertEqual(voice.single_media(self.root),(voice.MEDIA,self.extra))
        self.assertEqual(len(ops.inspection_files(self.root,True)),6)
        (self.root/'message/media_0.db-unknown.material').write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError,'media_0.db-unknown.material'):
            voice.single_media(self.root)

    def test_restore_uses_exact_original_association_not_shard_number(self):
        routes=voice.target_routing(self.root,A)
        for rel in (voice.MEDIA,self.extra):
            with db(self.root/rel) as c:c.execute('DELETE FROM VoiceInfo')
        result=voice.restore(self.root,A,self.messages,{101:51,102:52},{101,102},{101:AUDIO1,102:AUDIO2},routes=routes)
        self.assertEqual(result['voice_added'],2)
        for rel,sid,lid in ((voice.MEDIA,101,51),(self.extra,102,52)):
            with db(self.root/rel) as c:self.assertEqual(c.execute('SELECT local_id FROM VoiceInfo WHERE svr_id=?',(sid,)).fetchone(),(lid,))
        self.messages[0]['fields'].update(server_id=103,create_time=1700000150)
        with self.assertRaisesRegex(ValueError,'原有关联证据'):
            voice.restore(self.root,A,self.messages[:1],{103:53},{103},{103:AUDIO1},routes=routes)
        self.messages[0]['fields'].update(server_id=101)
        with self.assertRaisesRegex(ValueError,'原有关联证据'):
            voice.restore(self.root,B,self.messages[:1],{101:53},{101},{101:AUDIO1},routes=routes)

    def test_insufficient_evidence_and_mac_extra_shard_refused(self):
        with self.assertRaisesRegex(ValueError,'原有关联证据'):
            voice.restore(self.root,B,self.messages,{101:1,102:2},{101,102},{101:AUDIO1,102:AUDIO2})
        with patch('platform.system',return_value='Darwin'),self.assertRaisesRegex(ValueError,'单个'):
            voice.single_media(self.root)

    def test_new_capability_rejects_old_plan_and_orphan_sidecars(self):
        caps=windows_capabilities(True,False)
        self.assertIn('windows-voice-shards-v1',caps)
        with self.assertRaises(ValueError):windows_options({'version':5,'capabilities':[c for c in caps if c!='windows-voice-shards-v1']})
        put(self.root,'message/media_9.db-wal',b'synthetic')
        with self.assertRaisesRegex(ValueError,'sidecar'):voice.single_media(self.root)


class ImageContainerTests(unittest.TestCase):
    def test_bmp_plain_xor_and_corrupt_length(self):
        body=b'BM'+struct.pack('<IHHI',58,0,0,54)+struct.pack('<IiiHHIIiiII',40,1,1,1,24,0,4,0,0,0,0)+b'\x20\x40\x80\0'
        context=att.Images(Path('.'),'synthetic')
        self.assertEqual(att.image_format(body),'bmp')
        self.assertEqual(context.decode(bytes(b^0x64 for b in body))[0],body)
        for broken in (body[:-1],body[:2]+b'\0'*4+body[6:]):
            with self.assertRaises(ValueError):att.image_format(broken)

    def test_compatible_avif_brand_plain_xor_and_truncated_box(self):
        body=struct.pack('>I',24)+b'ftypzzzz\0\0\0\0avifmif1'+struct.pack('>I',12)+b'mdatdata'
        context=att.Images(Path('.'),'synthetic')
        self.assertEqual(att.image_format(body),'heif')
        self.assertEqual(context.decode(bytes(b^0x42 for b in body))[0],body)
        with self.assertRaises(ValueError):att.image_format(body[:-1])

    def test_target_xor_evidence_required_and_watched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);context=att.Images(root,A)
            with self.assertRaisesRegex(ValueError,'样本'):context.encode(PNG,{'format':'xor','xor':90})
            relative=att.destination(3,B,'2023-11','a'*32+'.dat')
            put(root,relative,bytes(b^41 for b in PNG))
            result=context.encode(PNG,{'format':'xor','xor':90})
            self.assertEqual(result,bytes(b^41 for b in PNG))
            self.assertIn(relative,context.dependencies)
