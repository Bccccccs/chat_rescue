"""Synthetic containers only; no decoding or client playback claims."""
import struct
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from chat_rescue import voice, attachments


def wav():
    chunks = b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, 8000, 16000, 2, 16)
    chunks += b'data' + struct.pack('<I', 4) + b'\0' * 4
    return b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks


class ContainerTests(unittest.TestCase):
    def test_windows_voice_accepts_standard_silk_wav_and_framed_amr(self):
        for body in (b'#!SILK_V3' + b'synthetic', wav(), b'#!AMR\n' + b'\x04' + b'\0' * 12):
            with self.subTest(header=body[:12]), patch('platform.system', return_value='Windows'):
                self.assertEqual(len(voice.audio_check(body)), 64)

    def test_windows_voice_rejects_truncated_wav_amr_and_unknown_bytes(self):
        for body in (wav()[:-1], b'#!AMR\n\x04\0', b'#!AMR\n\x7c', b'unknown-audio'):
            with self.subTest(header=body[:12]), patch('platform.system', return_value='Windows'):
                with self.assertRaises(ValueError):
                    voice.audio_check(body)

    def test_mac_voice_boundary_is_unchanged(self):
        with patch('platform.system', return_value='Darwin'):
            with self.assertRaises(ValueError): voice.audio_check(wav())

    def test_legacy_bundle_cannot_claim_wav_using_silk_capability(self):
        data={'version':2,'capabilities':['mac-voice-silk-v1'],
              'messages':[{'fields':{'server_id':1,'local_id':1,'local_type':34,'create_time':1,
                                    'status':3,'upload_status':0,'download_status':1}}]}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'voice/000001.wav';path.parent.mkdir();path.write_bytes(wav())
            data['voices']=[{'server_id':1,'local_id':1,'create_time':1,'data_index':'0',
                             'audio':{'path':'voice/000001.wav','size':len(wav()),'sha256':hashlib.sha256(wav()).hexdigest()}}]
            with patch('platform.system',return_value='Windows'),self.assertRaises(ValueError):
                voice.read_entries(tmp,data)

    def test_mov_and_avi_are_primary_video_but_thumbnails_are_not(self):
        with patch('platform.system', return_value='Windows'):
            for name in ('a.mov', 'a.M4V', 'a.avi'):
                self.assertTrue(attachments.primary(43, [(name,)]), name)
        self.assertFalse(attachments.primary(43, [('a_t.jpg',)]))

    def test_video_boxes_brand_mismatch_and_truncation(self):
        from chat_rescue.media_containers import video_format
        for brand, suffix in ((b'isom', '.mp4'), (b'qt  ', '.mov'), (b'M4V ', '.m4v')):
            body = struct.pack('>I', 20) + b'ftyp' + brand + b'\0'*4 + brand
            body += struct.pack('>I', 12) + b'mdat' + b'data'
            with patch('platform.system', return_value='Windows'):
                attachments.check_video(body, 'synthetic' + suffix)
                with self.assertRaises(ValueError): attachments.check_video(body[:-1], 'synthetic' + suffix)
            self.assertIn(video_format(body), ('mov', 'mp4'))
        with patch('platform.system', return_value='Windows'), self.assertRaises(ValueError):
            attachments.check_video(body, 'synthetic.avi')

    def test_avi_lists_and_length(self):
        from chat_rescue.media_containers import video_format
        chunks = b'LIST' + struct.pack('<I', 8) + b'hdrltest'
        chunks += b'LIST' + struct.pack('<I', 8) + b'movidata'
        body = b'RIFF' + struct.pack('<I', 4+len(chunks)) + b'AVI ' + chunks
        self.assertEqual(video_format(body), 'avi')
        with self.assertRaises(ValueError): video_format(body[:-1])

    def test_unknown_video_companion_cannot_bypass_container_checks(self):
        with patch('platform.system',return_value='Windows'),self.assertRaises(ValueError):
            attachments.check_video(b'unknown bytes','synthetic.unknown')

    def test_matroska_and_webm_have_tracks_and_clusters(self):
        from chat_rescue.media_containers import video_format
        for name in (b'matroska', b'webm'):
            header=b'\x42\x82'+bytes([0x80+len(name)])+name
            tracks=b'\x16\x54\xae\x6b\x81\0'
            cluster=b'\x1f\x43\xb6\x75\x81\0'
            segment=tracks+cluster
            body=b'\x1a\x45\xdf\xa3'+bytes([0x80+len(header)])+header
            body+=b'\x18\x53\x80\x67'+bytes([0x80+len(segment)])+segment
            self.assertEqual(video_format(body), 'webm' if name==b'webm' else 'mkv')
            with self.assertRaises(ValueError): video_format(body[:-1])

    def test_tiff_strip_offsets_and_jpeg_xl_boxes(self):
        # One-pixel TIFF with complete IFD / strip ranges, no decoder claim.
        fields=[(256,4,1,1),(257,4,1,1),(273,4,1,62),(279,4,1,1)]
        body=b'II*\0'+struct.pack('<I',8)+struct.pack('<H',len(fields))
        body+=b''.join(struct.pack('<HHII',*row) for row in fields)+b'\0'*4+b'\x80'
        self.assertEqual(attachments.image_format(body),'tiff')
        with self.assertRaises(ValueError): attachments.image_format(body[:-1])
        jxl=b'\0\0\0\x0cJXL \x0d\x0a\x87\x0a'+struct.pack('>I',12)+b'jxlc'+b'data'
        self.assertEqual(attachments.image_format(jxl),'jxl')
        with self.assertRaises(ValueError): attachments.image_format(jxl[:-1])

    @unittest.skipUnless(__import__('os').name=='nt','Windows CNG')
    def test_dat_distinguishes_missing_key_from_verified_unsupported_layout(self):
        from chat_rescue.media_containers import UnsupportedMediaFormat
        context=attachments.Images(Path('.'),'synthetic')
        raw=b'BM'+struct.pack('<IHHI',66,0,0,62)
        raw+=struct.pack('<IiiHHIIiiII',40,1,1,1,8,0,4,0,0,2,0)+b'\0'*12
        header=attachments.DAT2+struct.pack('<II',32,0)+b'\0'
        cipher=attachments.encode_dat(raw,header,b'1'*16,7)
        with patch.object(context,'candidates',return_value=iter([])):
            with self.assertRaisesRegex(ValueError,'缺少.*密钥元数据'):
                context.decode(cipher)
        with patch.object(context,'candidates',return_value=iter([(b'1'*16,7)])):
            with self.assertRaises(UnsupportedMediaFormat):context.decode(cipher)

    def test_dat_without_encrypted_or_xor_regions_needs_no_image_key(self):
        from test_attachments import PNG
        context=attachments.Images(Path('.'),'synthetic')
        header=attachments.DAT2+struct.pack('<II',0,0)+b'\0'
        with patch.object(context,'candidates',side_effect=AssertionError('no key required')):
            body,encoding=context.decode(header+PNG)
            self.assertEqual(body,PNG)
            self.assertEqual(context.encode(body,encoding),header+PNG)

    @unittest.skipUnless(__import__('os').name=='nt','Windows CNG')
    def test_v1_jxl_sample_does_not_claim_verified_tail_key(self):
        from test_attachments import put
        jxl=b'\0\0\0\x0cJXL \x0d\x0a\x87\x0a'+struct.pack('>I',12)+b'jxlc'+b'data'
        header=attachments.DAT1+struct.pack('<II',16,8)+b'\0'
        cipher=attachments.encode_dat(jxl,header,b'cfcd208495d565ef',37)
        with tempfile.TemporaryDirectory() as tmp:
            put(Path(tmp),'msg/attach/'+('a'*32)+'/2023-11/Img/'+('b'*32)+'.dat',cipher)
            context=attachments.Images(Path(tmp),'synthetic')
            with self.assertRaisesRegex(ValueError,'样本'):context.verified_v1()

    def test_supported_image_extensions_reach_container_validation(self):
        for suffix in ('bmp','avif','tif','tiff','jxl','wxgf','PNG','unknown'):
            self.assertTrue(attachments.destination(3,'synthetic','2023-11','a'*32+'.'+suffix).endswith('.'+suffix))

    def test_non_pcm_wav_is_unsupported_instead_of_malformed(self):
        from chat_rescue.media_containers import audio_format,UnsupportedMediaFormat
        raw=bytearray(wav());struct.pack_into('<H',raw,20,3)
        with self.assertRaises(UnsupportedMediaFormat):audio_format(bytes(raw))
