"""Pinned client fixture checks. Never executes a client."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from chat_rescue import windows_backend as backend


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'client'; self.source.mkdir()
        (self.source / 'Weixin.exe').write_bytes(b'synthetic executable - never run')
        (self.source / 'runtime.dll').write_bytes(b'synthetic dependency')
        self.value = {'version':1, 'executable':'Weixin.exe', 'file_version':'4.2.0.12',
                      'source_url':'https://dldir1.qq.com/weixin/Windows/synthetic.exe',
                      'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.iterdir()}}
        self.manifest = self.root / 'client-manifest.json'
        self.save()

    def save(self):
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')

    def test_fixed_client_refuses_extra_tampered_and_escaping_files(self):
        from chat_rescue import windows_client as client
        with patch.object(client, 'app_info', return_value={'file_version':'4.2.0.12', 'signature':'Valid'}):
            self.assertEqual(client.validate(self.source, self.manifest)['file_version'], '4.2.0.12')
            extra = self.source / 'private.db'; extra.write_bytes(b'not distributable')
            with self.assertRaises(ValueError): client.validate(self.source, self.manifest)
            extra.unlink()
            (self.source / 'runtime.dll').write_bytes(b'changed')
            with self.assertRaises(ValueError): client.validate(self.source, self.manifest)
            self.value['files']['../outside.dll'] = '0'*64; self.save()
            with self.assertRaises(ValueError): client.validate(self.source, self.manifest)

    def test_version_and_manifest_pin_are_enforced(self):
        from chat_rescue import windows_client as client
        with patch.object(client, 'app_info', return_value={'file_version':'4.2.0.13', 'signature':'Valid'}):
            with self.assertRaises(ValueError): client.validate(self.source, self.manifest)
        with self.assertRaises(ValueError): client.validate(self.source, self.manifest, '0'*64)

    def test_installed_client_is_complete_relocatable_and_read_only_prepared(self):
        from chat_rescue import windows_client as client
        payload = self.root / 'payload'; payload.mkdir()
        with patch.object(client, 'app_info', return_value={'file_version':'4.2.0.12', 'signature':'Valid'}):
            result = client.install(self.source, self.manifest, payload)
            self.assertEqual(result['executable'], 'wechat/Weixin.exe')
            self.assertTrue((payload / 'wechat/runtime.dll').is_file())
            self.assertEqual(hashlib.sha256((payload / 'wechat-manifest.json').read_bytes()).hexdigest(), result['client_manifest_sha256'])

    def test_pinned_prepare_cannot_select_another_executable(self):
        with patch.dict('os.environ', CHAT_RESCUE_CLIENT_MANIFEST=str(self.manifest),
                        CHAT_RESCUE_CLIENT_SHA256=hashlib.sha256(self.manifest.read_bytes()).hexdigest()), \
             patch.object(backend, 'app_info') as inspect:
            with self.assertRaises(ValueError):
                backend.dispatch({'action':'prepare', 'executable':str(self.source / 'Weixin.exe')})
            inspect.assert_not_called()
