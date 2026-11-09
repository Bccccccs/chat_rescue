"""Synthetic Config.Cipher objects and our own process only. No real WeChat."""
import ctypes as C
import hashlib
import hmac
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from chat_rescue import windows_capture as capture
from chat_rescue.workflow import private_json
from chat_rescue.operations import ALL


def page_for(key, salt):
    page = salt + os.urandom(4016)
    mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(x ^ 0x3a for x in salt), 2, 32)
    return page + hmac.new(mac_key, page[16:] + b'\1\0\0\0', hashlib.sha512).digest()


class Memory:
    base = 0x10000
    def __init__(self, count=4, corrupt=False, key_only=False, mismatched_salt=False, wrong_keys=False, rels=None):
        self.data = bytearray(1024 * 1024 + 4096)
        self.pages, self.keys = {}, {}
        # A split needle verifies chunk overlap handling.
        needle = self.base + 1024 * 1024 - 8
        self.put(needle, capture.NEEDLE)
        for i, rel in enumerate(rels or ALL):
            key, salt = os.urandom(32), os.urandom(16)
            self.keys[rel] = key.hex(); self.pages[rel] = page_for(key, salt)
            if i >= count: continue
            node, obj, blob = [self.base + offset + i * 0x400 for offset in (0x100, 0x200, 0x300)]
            self.put(node + 0x10, struct.pack('<QQ', needle, len(capture.NEEDLE)))
            self.put(node + 0x28, struct.pack('<Q', obj))
            literal_key=os.urandom(32) if wrong_keys else key
            literal_salt=os.urandom(16) if mismatched_salt else salt
            literal = b"x'" + (literal_key if key_only else literal_key + literal_salt).hex().encode() + b"'"
            encoded = bytes(v ^ capture.MASK[j % len(capture.MASK)] for j, v in enumerate(literal))
            if corrupt: encoded = b'\0' * len(encoded)
            self.put(obj + 0x90, struct.pack('<QQ', blob, len(encoded)))
            self.put(blob, encoded)
    def put(self, address, data):
        offset = address - self.base; self.data[offset:offset + len(data)] = data
    def regions(self): return [(self.base, len(self.data))]
    def read(self, address, size):
        offset = address - self.base
        return bytes(self.data[offset:offset + size]) if 0 <= offset and offset + size <= len(self.data) else b''


class ScanTests(unittest.TestCase):
    def test_eleven_selected_libraries_are_all_hmac_verified_on_salt_mismatch(self):
        rels=(*ALL, *(f'message/message_{i}.db' for i in range(1,7)), 'message/media_0.db')
        memory=Memory(count=11,mismatched_salt=True,rels=rels)
        self.assertEqual(len(memory.pages),11)
        self.assertEqual(capture.scan(memory,memory.pages,time.monotonic()+10),memory.keys)

    def test_embedded_salt_is_only_a_hint_actual_page_hmac_decides(self):
        memory=Memory(mismatched_salt=True)
        self.assertEqual(capture.scan(memory,memory.pages,time.monotonic()+10),memory.keys)

    def test_unrelated_account_literals_still_run_hmac_and_do_not_pass(self):
        memory=Memory(mismatched_salt=True,wrong_keys=True)
        with self.assertRaises(capture.ScanFailure) as failure:
            capture.scan(memory,memory.pages,time.monotonic()+10)
        report=failure.exception.diagnostic
        self.assertEqual(report['verified_count'],0)
        self.assertGreater(report['counts']['hmac_checks'],0)
        self.assertEqual(report['counts']['salted_literals_without_selected_salt'],4)
        self.assertEqual(report['reason'],'no-selected-salt-match')

    def test_key_only_literals_still_require_each_database_hmac(self):
        memory=Memory(key_only=True)
        self.assertEqual(capture.scan(memory,memory.pages,time.monotonic()+10),memory.keys)

    def test_partial_failure_reports_coverage_without_keys_salts_or_addresses(self):
        import json
        memory=Memory(count=3)
        with self.assertRaises(RuntimeError) as failure:
            capture.scan(memory,memory.pages,time.monotonic()+10)
        report=getattr(failure.exception,'diagnostic',None)
        self.assertIsInstance(report,dict)
        self.assertEqual((report['required_count'],report['verified_count']),(4,3))
        self.assertEqual(report['missing_databases'],[ALL[-1]])
        encoded=json.dumps(report)
        for key in memory.keys.values():self.assertNotIn(key,encoded)
        for page in memory.pages.values():self.assertNotIn(page[:16].hex(),encoded)
        self.assertIn('3/4',str(failure.exception))

    def test_more_shards_are_counted_instead_of_hardcoded_four(self):
        memory=Memory()
        memory.pages['message/message_7.db']=page_for(os.urandom(32),os.urandom(16))
        with self.assertRaisesRegex(RuntimeError,'4/5') as failure:
            capture.scan(memory,memory.pages,time.monotonic()+10)
        self.assertEqual(failure.exception.diagnostic['missing_databases'],['message/message_7.db'])

    def test_all_four_hmacs_and_split_feature(self):
        memory = Memory()
        self.assertEqual(capture.scan(memory, memory.pages, time.monotonic() + 10), memory.keys)
    def test_partial_and_corrupt_results_fail(self):
        for memory in (Memory(count=3), Memory(corrupt=True)):
            with self.assertRaises(RuntimeError):
                capture.scan(memory, memory.pages, time.monotonic() + 10)
    def test_expired_scan_fails(self):
        memory = Memory()
        with self.assertRaises(RuntimeError): capture.scan(memory, memory.pages, time.monotonic() - 1)
    def test_oversized_blob_is_rejected(self):
        memory = Memory(count=1)
        memory.put(memory.base + 0x200 + 0x98, struct.pack('<Q', 1025))
        with self.assertRaises(RuntimeError): capture.scan(memory, memory.pages, time.monotonic() + 10)


@unittest.skipUnless(os.name == 'nt', 'Windows native read-only services')
class NativeTests(unittest.TestCase):
    def test_mismatched_embedded_salt_capture_passes_real_encrypted_full_integrity(self):
        from chat_rescue.vendor.sqlcipher_probe import DB
        from chat_rescue.runtime import make_private
        import json
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); make_private(base)
            root=base/'synthetic_owner'/'db_storage'; root.mkdir(parents=True)
            exe=base/'Synthetic.exe'; exe.write_bytes(b'synthetic fixture only')
            memory=Memory(mismatched_salt=True)
            memory.closed=False
            memory.close=lambda: setattr(memory,'closed',True)
            for rel,key in memory.keys.items():
                path=root/rel; path.parent.mkdir(parents=True,exist_ok=True)
                with DB(path,key,fixture_write=True) as database:
                    database.query('CREATE TABLE synthetic(id INTEGER PRIMARY KEY);')
                with path.open('rb') as stream: memory.pages[rel]=stream.read(4096)
            output=base/'keys.json'
            metadata={'executable':str(exe),'sha256':capture.digest(exe),'file_version':'4.2.0.50'}
            with patch.object(capture,'app_info',return_value=metadata),patch.object(capture,'Process',return_value=memory):
                result=capture.capture(123,str(exe),root,'synthetic_owner',output)
            self.assertTrue(memory.closed)
            self.assertEqual(result['verified_required_databases'],4)
            self.assertEqual(json.loads(output.read_text())['keys'],{rel:{'enc_key':key} for rel,key in memory.keys.items()})
            self.assertEqual(json.loads(output.with_suffix('.capture.json').read_text())['file_version'],'4.2.0.50')

    def test_reads_only_our_synthetic_process_and_closes_handle(self):
        data = C.create_string_buffer(b'synthetic-only')
        process = capture.Process(os.getpid(), sys.executable)
        try:
            self.assertEqual(process.read(C.addressof(data), 14), b'synthetic-only')
            regions = list(process.regions())
            self.assertTrue(any(base <= C.addressof(data) < base + size for base, size in regions))
        finally: process.close()
        self.assertIsNone(process.handle)
    def test_wrong_image_is_rejected(self):
        with self.assertRaises(ValueError): capture.Process(os.getpid(), 'synthetic.exe')
    def test_version_gate_refuses_non_target_without_reading_processes(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'Weixin.exe'; shutil.copy2(sys.executable, exe)
            with self.assertRaisesRegex(ValueError, '4.2.x.x'):
                capture.app_info(exe)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.root = self.directory / 'synthetic_owner/db_storage'
        self.memory = Memory()
        for rel, page in self.memory.pages.items():
            p = self.root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(page)
        self.exe = self.directory / 'Weixin.exe'; self.exe.write_bytes(b'synthetic')
        self.output = self.directory / 'keys.json'
    def invoke(self, keys, integrity='ok', scan_error=None):
        metadata = {'executable': str(self.exe), 'sha256': capture.digest(self.exe), 'file_version': '4.2.0.11'}
        with patch.object(capture, 'app_info', return_value=metadata), \
             patch.object(capture, 'Process') as process, \
             patch.object(capture, 'scan', return_value=keys, side_effect=scan_error), \
             patch.object(capture, 'require_private'), \
             patch('chat_rescue.vendor.sqlcipher_probe.DB') as database:
            database.return_value.__enter__.return_value.query.return_value = [{'integrity_check': integrity}]
            result = capture.capture(123, self.exe, self.root, 'synthetic_owner', self.output)
            process.return_value.close.assert_called_once()
            self.assertEqual(database.call_count, 4)
            return result
    def test_partial_scan_does_not_write_manifest(self):
        with self.assertRaises(RuntimeError): self.invoke({ALL[0]: 'ab' * 32})
        self.assertFalse(self.output.exists())
    def test_complete_scan_saves_private_keys_and_receipt(self):
        result = self.invoke(self.memory.keys)
        self.assertEqual(result['verified_required_databases'], 4)
        self.assertTrue(self.output.with_suffix('.capture.json').is_file())
    def test_existing_output_is_not_overwritten(self):
        private_json(self.output, {'synthetic': True})
        before = self.output.read_bytes()
        with self.assertRaises(FileExistsError): self.invoke(self.memory.keys)
        self.assertEqual(self.output.read_bytes(), before)
    def test_database_integrity_failure_does_not_save_keys(self):
        with self.assertRaises(RuntimeError): self.invoke(self.memory.keys, integrity='synthetic failure')
        self.assertFalse(self.output.exists())
    def test_scan_failure_closes_process_and_saves_nothing(self):
        metadata = {'executable': str(self.exe), 'sha256': capture.digest(self.exe)}
        with patch.object(capture, 'app_info', return_value=metadata), \
             patch.object(capture, 'require_private'), patch.object(capture, 'Process') as process, \
             patch.object(capture, 'scan', side_effect=RuntimeError('synthetic timeout')):
            with self.assertRaises(RuntimeError):
                capture.capture(123, self.exe, self.root, 'synthetic_owner', self.output)
            process.return_value.close.assert_called_once()
        self.assertFalse(self.output.exists())

    def test_hmac_failure_saves_redacted_private_diagnostic_only(self):
        import json
        memory=Memory(count=3)
        try:capture.scan(memory,memory.pages,time.monotonic()+10)
        except capture.ScanFailure as error:failure=error
        with self.assertRaisesRegex(RuntimeError,'诊断报告'):
            self.invoke({},scan_error=failure)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix('.capture.json').exists())
        report_path=self.output.with_suffix('.capture-failed.json')
        report=json.loads(report_path.read_text(encoding='utf-8'))
        self.assertFalse(report['keys_saved'])
        self.assertEqual(report['verified_count'],3)
        text=report_path.read_text(encoding='utf-8')
        self.assertNotIn(str(self.root),text)
        for value in memory.keys.values():self.assertNotIn(value,text)
        from chat_rescue.runtime import require_private
        require_private(report_path)
