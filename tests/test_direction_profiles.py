"""Synthetic fixtures for the separately observed Windows text direction profile."""
import json
from pathlib import Path
import tempfile
import unittest
from chat_rescue.workflow import (WINDOWS_TEXT_PROFILE, WINDOWS_STRUCTURED_PROFILE, WINDOWS_PARTICIPANT_PROFILE, LEGACY_PROFILE, flip_packed, private_json,
                                  prepare_plain_restore, save_chat, table_for, connect)
from chat_rescue.operations import capture_direction_profile
from chat_rescue.windows_version import is_windows_42_version
from test_workflow import fixture, db, A, B


class DirectionProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.target = self.root / 'source', self.root / 'target'
        fixture(self.source, True); fixture(self.target, False)

    def windows_source(self):
        with db(self.source / 'message/message_0.db') as c:
            c.execute('UPDATE ' + table_for(A) + ' SET local_type=1,status=3')
            c.execute('UPDATE ' + table_for(A) + ' SET packed_info_data=?,status=2 WHERE server_id=102',
                      (bytes.fromhex('080910035800'),))
        with db(self.source / 'message/message_resource.db') as c:
            c.execute('DELETE FROM MessageResourceDetail'); c.execute('DELETE FROM MessageResourceInfo')
        private_json(self.source / 'direction-profile.json', {'direction_profile': WINDOWS_TEXT_PROFILE})

    def test_legacy_still_rejects_three_and_wrong_windows_perspective(self):
        for data, outgoing, kwargs in ((b'\x08\x09\x10\x03', False, {}),
                                      (b'\x08\x09\x10\x03', True, {'source_outgoing': 3}),
                                      (b'\x08\x09\x10\x01', False, {'source_outgoing': 3})):
            with self.assertRaisesRegex(ValueError, 'opposite perspective'):
                flip_packed(data, 9, 4, outgoing, **kwargs)

    def test_windows_restore_both_directions_status_unknown_fields_and_repeat(self):
        self.windows_source()
        private_json(self.target / 'direction-profile.json', {'direction_profile': WINDOWS_TEXT_PROFILE})
        bundle = self.root / 'bundle'; save_chat(self.source, B, A, bundle)
        result = prepare_plain_restore(bundle, self.target, A, self.root / 'first')
        self.assertEqual(result['added'], 2)
        with connect(self.root / 'first/message/message_0.db') as c:
            rows = c.execute('SELECT server_id,real_sender_id,status,hex(packed_info_data) FROM ' + table_for(B) + ' ORDER BY server_id').fetchall()
            self.assertEqual([tuple(r) for r in rows], [(101, 4, 2, '080410033A0178'), (102, 302, 3, '08AE0210025800')])
        again = prepare_plain_restore(bundle, self.root / 'first', A, self.root / 'second')
        self.assertEqual((again['added'], again['skipped']), (0, 2))

    def test_windows_to_legacy_uses_target_flag(self):
        self.windows_source()
        bundle = self.root / 'bundle'; save_chat(self.source, B, A, bundle)
        prepare_plain_restore(bundle, self.target, A, self.root / 'result')
        with connect(self.root / 'result/message/message_0.db') as c:
            self.assertEqual(c.execute('SELECT hex(packed_info_data),status FROM ' + table_for(B) + ' WHERE server_id=101').fetchone()[:], ('080410013A0178', 3))

    def test_windows_one_second_local_time_variant_deduplicates_without_edit(self):
        self.windows_source()
        private_json(self.target / 'direction-profile.json', {'direction_profile': WINDOWS_TEXT_PROFILE})
        bundle = self.root / 'bundle'; save_chat(self.source, B, A, bundle)
        first = self.root / 'first'; prepare_plain_restore(bundle, self.target, A, first)
        with db(first / 'message/message_0.db') as c:
            c.execute('UPDATE ' + table_for(B) + ' SET create_time=create_time+1,sort_seq=sort_seq+1000 WHERE server_id=102')
            before = c.execute('SELECT * FROM ' + table_for(B) + ' ORDER BY server_id').fetchall()
        result = prepare_plain_restore(bundle, first, A, self.root / 'second')
        self.assertEqual((result['added'], result['skipped'], result['timestamp_variants_preserved']), (0, 2, 1))
        with db(self.root / 'second/message/message_0.db') as c:
            self.assertEqual(c.execute('SELECT * FROM ' + table_for(B) + ' ORDER BY server_id').fetchall(), before)
        for n, sql in enumerate(('create_time=create_time+2,sort_seq=sort_seq+2000',
                                 'create_time=create_time+1',
                                 "create_time=create_time+1,sort_seq=sort_seq+1000,message_content='changed'")):
            with db(first / 'message/message_0.db') as c:
                c.execute('UPDATE ' + table_for(B) + ' SET create_time=1700000000,sort_seq=1700000000000 WHERE server_id=101')
                c.execute('UPDATE ' + table_for(B) + ' SET ' + sql + ' WHERE server_id=101')
            with self.assertRaisesRegex(ValueError, 'collision'):
                prepare_plain_restore(bundle, first, A, self.root / ('bad' + str(n)))

    def test_legacy_to_windows_text_uses_target_flag(self):
        with db(self.source / 'message/message_0.db') as c:
            c.execute('UPDATE ' + table_for(A) + ' SET local_type=1')
        with db(self.source / 'message/message_resource.db') as c:
            c.execute('UPDATE MessageResourceInfo SET message_local_type=1')
        private_json(self.target / 'direction-profile.json', {'direction_profile': WINDOWS_TEXT_PROFILE})
        bundle = self.root / 'bundle'; save_chat(self.source, B, A, bundle)
        prepare_plain_restore(bundle, self.target, A, self.root / 'result')
        with connect(self.root / 'result/message/message_0.db') as c:
            self.assertEqual(c.execute('SELECT hex(packed_info_data),status FROM ' + table_for(B) + ' WHERE server_id=101').fetchone()[:], ('080410033A0178', 2))

    def test_windows_unobserved_message_types_and_unknown_profiles_refused(self):
        private_json(self.source / 'direction-profile.json', {'direction_profile': WINDOWS_TEXT_PROFILE})
        with self.assertRaisesRegex(ValueError, 'text directions only'):
            save_chat(self.source, B, A, self.root / 'bundle')
        (self.source / 'direction-profile.json').write_text(json.dumps({'direction_profile': 'unknown'}))
        with self.assertRaisesRegex(ValueError, 'unsupported message direction profile'):
            save_chat(self.source, B, A, self.root / 'bundle2')

    def test_capture_receipt_must_bind_owner_root_and_42_version(self):
        root = self.root / B / 'db_storage'; root.parent.mkdir(parents=True); fixture(root, True)
        keys = self.root / 'keys.json'
        self.assertEqual(capture_direction_profile(root, keys), LEGACY_PROFILE)
        receipt = keys.with_suffix('.capture.json')
        valid = {'state': 'verified', 'verified_required_databases': 4, 'database_root': str(root),
                 'owner': B, 'file_version': '4.2.0.12'}
        for change in ({'owner': A}, {'database_root': str(self.target)}, {'state': 'partial'},
                       {'verified_required_databases': 3}, {'file_version': '4.1.9.99'},
                       {'file_version': '4.3.0.12'}, {'file_version': '4.2'}, {'file_version': None}):
            receipt.write_text(json.dumps({**valid, **change}), encoding='utf-8')
            with self.assertRaises(ValueError): capture_direction_profile(root, keys)
        for version in ('4.2.0.12', '4.2.0.99', '4.2.1.0', '4.2.99.123'):
            with self.subTest(version=version):
                receipt.write_text(json.dumps({**valid, 'file_version': version}), encoding='utf-8')
                self.assertEqual(capture_direction_profile(root, keys), WINDOWS_STRUCTURED_PROFILE)
        receipt.write_text(json.dumps({**valid, 'file_version':'4.2.0.13'}),encoding='utf-8')
        self.assertEqual(capture_direction_profile(root,keys),WINDOWS_PARTICIPANT_PROFILE)

    def test_complete_42_versions_only(self):
        for version in ('4.2.0.0', '4.2.0.12', '4.2.1.25', '4.2.99.999'):
            with self.subTest(version=version):
                self.assertTrue(is_windows_42_version(version))
        for version in (None, 4.2, '', '4.2', '4.2.0', '4.2.0.12beta',
                        '4.2.0.12\n', '4.20.0.12', '4.3.0.12', '5.2.0.12'):
            with self.subTest(version=version):
                self.assertFalse(is_windows_42_version(version))

    def test_old_windows_export_without_profile_reproduces_direction_error(self):
        self.windows_source()
        (self.source / 'direction-profile.json').unlink()
        with self.assertRaisesRegex(ValueError, 'opposite perspective'):
            save_chat(self.source, B, A, self.root / 'old-bundle')
        private_json(self.source / 'direction-profile.json', {'direction_profile': WINDOWS_TEXT_PROFILE})
        bundle = self.root / 'new-bundle'
        save_chat(self.source, B, A, bundle)
        self.assertEqual(prepare_plain_restore(bundle, self.target, A, self.root / 'restored')['added'], 2)
