"""Installer inputs remain relocatable and exclude account/runtime history."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('windows_package', Path(__file__).resolve().parents[1] / 'desktop/windows/package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class WindowsPackageTests(unittest.TestCase):
    def test_config_uses_relative_runtime_and_no_workspace_or_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package.write_config(root)
            config = json.loads((root / 'backend-config.json').read_text())
            self.assertEqual(set(config), {'python', 'sqlcipher', 'repository', 'executable'})
            self.assertEqual(config['executable'], '')
            self.assertTrue(all(not Path(value).is_absolute() for value in config.values()))

    def test_mismatched_dependency_archive_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root / 'dependency.zip'; path.write_bytes(b'synthetic corrupt download')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                package.archive('https://example.invalid/archive.zip', '0' * 64, root, path)

    def test_only_tracked_public_file_types_are_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); payload = root / 'payload'; payload.mkdir()
            names = ['chat_rescue/runtime.py', 'chat_rescue/private.db', 'chat_rescue/keys.json',
                     'chat_rescue/vendor/LICENSE', 'docs/windows.md', 'docs/private.jpg',
                     'desktop/windows/licenses/SQLCipher-LICENSE.txt']
            for name in names + ['chat_rescue/untracked-secret.py']:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('synthetic')
            with patch.object(package, 'ROOT', root), patch.object(package.subprocess, 'check_output', return_value=('\0'.join(names) + '\0').encode()):
                package.copy_source(payload)
            copied = {p.relative_to(payload).as_posix() for p in payload.rglob('*') if p.is_file()}
            self.assertEqual(copied, {'app/chat_rescue/runtime.py', 'app/chat_rescue/vendor/LICENSE',
                                     'docs/windows.md', 'licenses/SQLCipher-LICENSE.txt'})
