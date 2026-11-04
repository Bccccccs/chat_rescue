"""A narrow, identity-based two-party WeChat recovery workflow."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from .runtime import make_private

RELS = ('message/message_0.db', 'message/message_resource.db', 'session/session.db')
SUPPORTED = {1, 42, (6 << 32) | 49}
LEGACY_PROFILE = 'legacy-v1'
WINDOWS_TEXT_PROFILE = 'windows-4.2.0.12-text'


def direction_profile(value):
    if value not in (LEGACY_PROFILE, WINDOWS_TEXT_PROFILE):
        raise ValueError('unsupported message direction profile')
    return value


def plain_profile(root):
    for name in ('export-report.json', 'direction-profile.json'):
        path = Path(root) / name
        if path.exists():
            return direction_profile(json.loads(path.read_text(encoding='utf-8')).get('direction_profile', LEGACY_PROFILE))
    return LEGACY_PROFILE


def outgoing_flag(profile):
    return 3 if direction_profile(profile) == WINDOWS_TEXT_PROFILE else 1


def check_profile_type(local_type, *profiles, include_voice=False, include_attachments=False):
    allowed = SUPPORTED | ({34} if include_voice else set()) | ({3,43} if include_attachments else set())
    if local_type not in allowed:
        raise ValueError('unsupported message type: ' + str(local_type))
    if WINDOWS_TEXT_PROFILE in profiles and local_type != 1:
        raise ValueError('Windows 4.2.0.12 currently supports verified text directions only')


def ident(name):
    return '"' + name.replace('"', '""') + '"'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def private_json(path, value):
    path = Path(path)
    with path.open('x', encoding='utf-8') as f:
        make_private(path)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def write_connect(path):
    return sqlite3.connect(path, factory=ClosingConnection)


def connect(path):
    c = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, factory=ClosingConnection)
    c.row_factory = sqlite3.Row
    return c


def pack(value):
    if isinstance(value, bytes):
        return {'base64': base64.b64encode(value).decode()}
    if isinstance(value, dict):
        return {k: pack(v) for k, v in value.items()}
    if isinstance(value, list):
        return [pack(v) for v in value]
    return value


def unpack(value):
    if isinstance(value, dict) and set(value) == {'base64'}:
        return base64.b64decode(value['base64'], validate=True)
    if isinstance(value, dict):
        return {k: unpack(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unpack(v) for v in value]
    return value


def table_for(username):
    return 'Msg_' + hashlib.md5(username.encode('utf-8')).hexdigest()


def varint(value):
    if value < 0:
        raise ValueError('negative protobuf integer')
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def readvar(data, pos):
    value = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError('truncated packed metadata')
        byte = data[pos]
        pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, pos
    raise ValueError('invalid packed integer')


def flip_packed(data, old_sender, new_sender, outgoing, *, source_outgoing=1, target_outgoing=1):
    if source_outgoing not in (1, 3) or target_outgoing not in (1, 3):
        raise ValueError('unsupported packed direction profile')
    if not isinstance(data, bytes):
        raise ValueError('packed direction metadata missing')
    result, seen, pos = [], [], 0
    while pos < len(data):
        start = pos
        tag, pos = readvar(data, pos)
        field, wire = tag >> 3, tag & 7
        value = None
        if wire == 0:
            value, pos = readvar(data, pos)
        elif wire in (1, 5):
            pos += 8 if wire == 1 else 4
        elif wire == 2:
            size, pos = readvar(data, pos)
            pos += size
        else:
            raise ValueError('unsupported packed wire type')
        if pos > len(data):
            raise ValueError('truncated packed field')
        if field == 1:
            if wire != 0 or value != old_sender:
                raise ValueError('packed sender conflicts with message identity')
            result.append(varint(tag) + varint(new_sender))
            seen.append(1)
        elif field == 2:
            wanted = target_outgoing if outgoing else 2
            expected = 2 if outgoing else source_outgoing
            if wire != 0 or value != expected:
                raise ValueError('packed direction is not the expected opposite perspective')
            result.append(varint(tag) + varint(wanted))
            seen.append(2)
        else:
            result.append(data[start:pos])
    if seen != [1, 2]:
        raise ValueError('unrecognized packed direction schema')
    return b''.join(result)


def _single_shard(root):
    shards = [p for p in (Path(root) / 'message').glob('message_*.db')
              if re.fullmatch(r'message_\d+\.db', p.name)]
    if len(shards) != 1 or shards[0].name != 'message_0.db':
        names = ', '.join(sorted(p.name for p in shards)) or '未找到编号消息库'
        raise ValueError('当前恢复写入仅支持单个 message_0.db；检测结果：' + names + '。多分片检查不代表已支持恢复，请保留全部分片')


def save_chat(plain_root, owner, peer, output, *, include_voice=False, allow_missing_voice=False, include_attachments=False, allow_missing_attachments=False, asset_root=None):
    os.umask(0o077)
    root, output = Path(plain_root), Path(output)
    if owner == peer or '@' in owner or '@' in peer:
        raise ValueError('only two different personal WeChat identities are supported')
    _single_shard(root)
    profile = plain_profile(root)
    with connect(root / 'contact/contact.db') as c:
        participants = [dict(r) for r in c.execute('SELECT * FROM contact WHERE username IN (?,?)', (owner, peer))]
        if {r['username'] for r in participants} != {owner, peer}:
            raise ValueError('owner/peer must be exact usernames in the contact database')
    table = table_for(peer)
    with connect(root / RELS[0]) as c:
        schema = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not schema:
            raise ValueError('selected conversation has no local message table')
        indexes = [r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,))]
        names = {r['rowid']: r['user_name'] for r in c.execute('SELECT rowid,user_name FROM Name2Id')}
        messages = []
        for raw in c.execute('SELECT * FROM ' + ident(table) + ' ORDER BY sort_seq,local_id'):
            row = dict(raw)
            sender = names.get(row['real_sender_id'])
            if sender not in (owner, peer):
                raise ValueError('message sender is outside the selected two parties')
            check_profile_type(row['local_type'], profile, include_voice=include_voice, include_attachments=include_attachments)
            if not isinstance(row['server_id'], int) or row['server_id'] <= 0:
                raise ValueError('a real positive server_id is required; no ID is fabricated')
            # Validate the observed opposite-account direction structure now.
            flip_packed(row['packed_info_data'], row['real_sender_id'], 1, sender == peer,
                        source_outgoing=outgoing_flag(profile), target_outgoing=outgoing_flag(profile))
            messages.append({'sender': sender, 'fields': row})
        if not messages or len({m['fields']['server_id'] for m in messages}) != len(messages):
            raise ValueError('conversation is empty or has duplicate server IDs')
    resources = []
    with connect(root / RELS[1]) as c:
        for m in messages:
            for row in c.execute('SELECT * FROM MessageResourceInfo WHERE message_svr_id=?', (m['fields']['server_id'],)):
                if (include_voice and m['fields']['local_type'] == 34) or (include_attachments and m['fields']['local_type'] in (3,43,(6<<32)|49)):
                    chat = c.execute('SELECT user_name FROM ChatName2Id WHERE rowid=?', (row['chat_id'],)).fetchone()
                    sender = c.execute('SELECT user_name FROM SenderName2Id WHERE rowid=?', (row['sender_id'],)).fetchone()
                    if (not chat or chat[0] != peer or not sender or sender[0] != m['sender']
                            or row['message_local_id'] != m['fields']['local_id']
                            or row['message_create_time'] != m['fields']['create_time']
                            or row['message_local_type'] != m['fields']['local_type']):
                        raise ValueError('voice resource source identity collision')
                resources.append({'info': dict(row), 'details': [dict(x) for x in c.execute(
                    'SELECT * FROM MessageResourceDetail WHERE message_id=? ORDER BY resource_id', (row['message_id'],))]})
    with connect(root / RELS[2]) as c:
        session = c.execute('SELECT * FROM SessionTable WHERE username=?', (peer,)).fetchone()
        if session is None:
            raise ValueError('source conversation session metadata missing')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    payload = {'version': 1, 'owner': owner, 'peer': peer, 'source_table': table, 'direction_profile': profile,
               'schema': schema[0], 'indexes': indexes, 'participants': participants,
               'messages': messages, 'resources': resources, 'session': dict(session)}
    if include_voice:
        from .voice import collect, CAPABILITY
        if profile != LEGACY_PROFILE:
            raise ValueError('语音实验模式仅适配 Mac 方向配置')
        payload.update(version=2, capabilities=[CAPABILITY],
                       voices=collect(root, peer, messages, output, allow_missing_voice))
    if include_attachments:
        from . import attachments as att
        if profile != LEGACY_PROFILE:
            raise ValueError('附件模式仅适配Mac方向配置')
        asset_root = att.resolve_root(root, asset_root)
        entries, originals = att.collect(root, asset_root, owner, peer, messages, output, allow_missing_attachments)
        payload.update(version=3, capabilities=payload.get('capabilities', [])+[att.CAPABILITY],
                       attachments=entries, attachment_originals=originals)
    private_json(output / 'conversation.json', pack(payload))
    private_json(output / 'manifest.json', {'version': payload['version'], 'sha256': digest(output / 'conversation.json'),
                                          'message_count': len(messages)})
    return {'saved': len(messages), 'owner': owner, 'peer': peer}


def _mapping(c, table, username, **extras):
    row = c.execute('SELECT rowid FROM ' + ident(table) + ' WHERE user_name=?', (username,)).fetchone()
    if row:
        return row[0]
    return _insert(c, table, {'user_name': username, **extras})


def _insert(c, table, row):
    return c.execute('INSERT INTO ' + ident(table) + ' (' + ','.join(ident(k) for k in row)
                     + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values())).lastrowid


def prepare_plain_restore(bundle, target_root, target_owner, output, *, include_voice=False, include_attachments=False, target_asset_root=None):
    os.umask(0o077)
    bundle, target_root, output = Path(bundle), Path(target_root), Path(output)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('version') not in ((1,2,3) if include_attachments and include_voice else (1,3) if include_attachments else (1,2) if include_voice else (1,))
            or digest(bundle / 'conversation.json') != manifest.get('sha256')):
        raise ValueError('bundle version/hash mismatch')
    data = unpack(json.loads((bundle / 'conversation.json').read_text(encoding='utf-8')))
    if data.get('version') != manifest['version'] or target_owner != data['peer']:
        raise ValueError('target owner must be the source conversation peer')
    source_profile = direction_profile(data.get('direction_profile', LEGACY_PROFILE))
    target_profile = plain_profile(target_root)
    if data['version'] == 1 and any(m['fields']['local_type'] == 34 for m in data['messages']):
        raise ValueError('旧 bundle 不允许携带语音')
    attachment_mode = include_attachments
    attachment_plan, attachment_counts = None, {}
    if data['version'] == 3:
        from .voice import CAPABILITY as VOICE_CAP
        from .attachments import CAPABILITY as ATTACH_CAP
        expected = ([VOICE_CAP] if include_voice else [])+[ATTACH_CAP]
        if data.get('capabilities') != expected:
            raise ValueError('附件bundle能力与本次显式开关不一致')
    voice_mode = include_voice
    audios = {}
    if voice_mode:
        from .voice import CAPABILITY, MEDIA, single_media, read_entries
        if ((data['version'] == 2 and data.get('capabilities') != [CAPABILITY])
                or source_profile != LEGACY_PROFILE or target_profile != LEGACY_PROFILE):
            raise ValueError('语音能力或方向配置不受支持')
        single_media(target_root)
        if data['version'] in (2,3):
            audios = read_entries(bundle, data)
    _single_shard(target_root)
    peer = data['owner']
    with connect(target_root / 'contact/contact.db') as c:
        target_contacts = {r['username']: dict(r) for r in c.execute('SELECT * FROM contact WHERE username IN (?,?)', (target_owner, peer))}
        users = set(target_contacts)
        if users != {target_owner, peer}:
            raise ValueError('log into target account and sync/open the peer contact first')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_rels = (*RELS, MEDIA) if voice_mode else RELS
    for rel in (*write_rels, 'contact/contact.db'):
        (output / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target_root / rel, output / rel)
    if attachment_mode:
        from . import attachments as att
        if source_profile != LEGACY_PROFILE or target_profile != LEGACY_PROFILE:
            raise ValueError('附件模式仅适配Mac方向配置')
        if data['version'] != 3 and any(m['fields']['local_type'] in (3,43,(6<<32)|49) for m in data['messages']):
            raise ValueError('旧bundle没有附件本体，请用附件模式重新导出')
        target_asset_root = att.resolve_root(target_root, target_asset_root)
        if data['version'] == 3:
            attachment_plan, attachment_counts = att.prepare(bundle, data, target_root, target_asset_root, target_owner, output)
        else:
            attachment_plan = {'originals':{}, 'candidates':{}}
    table = table_for(peer)
    local_ids, added_ids, skipped, timestamp_variants = {}, set(), 0, 0
    with write_connect(output / RELS[0]) as c:
        c.row_factory = sqlite3.Row
        own_row = c.execute('SELECT rowid FROM Name2Id WHERE user_name=?', (target_owner,)).fetchone()
        if not own_row:
            raise ValueError('target owner missing from message identity mapping')
        own_id = own_row[0]
        peer_id = _mapping(c, 'Name2Id', peer, is_session=1)
        if not c.execute('SELECT name FROM sqlite_master WHERE name=?', (table,)).fetchone():
            expected_source = table_for(data['peer'])
            if data['source_table'] != expected_source or not data['schema'].startswith('CREATE TABLE ' + expected_source + '('):
                raise ValueError('unexpected source table schema')
            c.execute(data['schema'].replace(expected_source, table))
            for sql in data['indexes']:
                if not sql.startswith('CREATE INDEX ' + expected_source + '_'):
                    raise ValueError('unexpected source index schema')
                c.execute(sql.replace(expected_source, table))
        for m in data['messages']:
            row = dict(m['fields'])
            check_profile_type(row['local_type'], source_profile, target_profile, include_voice=voice_mode, include_attachments=attachment_mode)
            if row['server_id'] <= 0 or m['sender'] not in (target_owner, peer):
                raise ValueError('invalid message in bundle')
            outgoing = m['sender'] == target_owner
            new_sender = own_id if outgoing else peer_id
            old_sender = row['real_sender_id']
            row.pop('local_id')
            row['real_sender_id'] = new_sender
            row['packed_info_data'] = flip_packed(row['packed_info_data'], old_sender, new_sender, outgoing,
                                                source_outgoing=outgoing_flag(source_profile),
                                                target_outgoing=outgoing_flag(target_profile))
            if row['local_type'] != 34 and not (attachment_mode and row['local_type'] in (3,43,(6<<32)|49)):
                row['status'] = 2 if target_profile == WINDOWS_TEXT_PROFILE and outgoing else 3
            hits = []
            for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"):
                hits += [(name, dict(r)) for r in c.execute('SELECT * FROM ' + ident(name) + ' WHERE server_id=?', (row['server_id'],))]
            if hits:
                if len(hits) != 1 or hits[0][0] != table:
                    raise ValueError('server ID collision in another conversation')
                existing = hits[0][1]
                if any(existing[k] != row[k] for k in ['message_content', 'compress_content', 'local_type', 'real_sender_id']):
                    raise ValueError('server ID collision with different message contents/identity')
                if existing['create_time'] != row['create_time']:
                    # The paired Windows text sample has one second of local receive
                    # latency, also reflected exactly in sort_seq. Keep each local
                    # record unchanged; this exception never applies across profiles.
                    delta = existing['create_time'] - row['create_time']
                    if (source_profile != WINDOWS_TEXT_PROFILE or target_profile != WINDOWS_TEXT_PROFILE
                            or abs(delta) != 1 or existing['sort_seq'] - row['sort_seq'] != delta * 1000):
                        raise ValueError('server ID collision with different message contents/identity')
                    timestamp_variants += 1
                if row['local_type'] == 34:
                    from .voice import message_state
                    message_state(existing)
                    # Playback/download flags are local; preserve observed target states.
                    if existing['packed_info_data'] != row['packed_info_data']:
                        raise ValueError('voice direction metadata collision')
                local_ids[row['server_id']] = existing['local_id']
                skipped += 1
                continue
            local_ids[row['server_id']] = _insert(c, table, row)
            added_ids.add(row['server_id'])
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='DeleteInfo'").fetchone():
            c.execute('DELETE FROM DeleteInfo WHERE chat_name_id=? OR delete_table_name=?', (peer_id, table))
        c.commit()
    attachment_resource_repaired = 0
    with write_connect(output / RELS[1]) as c:
        c.row_factory = sqlite3.Row
        voice_ids = set(audios)
        attachment_ids = {e['server_id'] for e in data.get('attachments',[]) if e['files']} if attachment_mode else set()
        for resource in data['resources']:
            info = dict(resource['info'])
            sid = info['message_svr_id']
            if sid not in added_ids and sid not in voice_ids and sid not in attachment_ids:
                continue
            sender = next(m['sender'] for m in data['messages'] if m['fields']['server_id'] == sid)
            existing_chats = c.execute(
                'SELECT chats.user_name FROM MessageResourceInfo AS resource '
                'LEFT JOIN ChatName2Id AS chats ON chats.rowid=resource.chat_id '
                'WHERE resource.message_svr_id=?', (sid,)).fetchall()
            # Resource caches can share a server ID across conversations. Keep
            # foreign caches intact, but reject ambiguous or same-chat entries.
            source_message = next(m['fields'] for m in data['messages'] if m['fields']['server_id'] == sid)
            if sid in voice_ids and (info['message_local_id'] != source_message['local_id']
                    or info['message_create_time'] != source_message['create_time']
                    or info['message_local_type'] != 34):
                raise ValueError('voice resource association collision')
            if sid in attachment_ids and existing_chats:
                from .attachments import merge_resource
                repairs = merge_resource(c, resource, peer, sender, local_ids[sid])
                if repairs is not None:
                    attachment_resource_repaired += repairs
                    continue
            if sid in voice_ids and existing_chats:
                same = c.execute('SELECT r.* FROM MessageResourceInfo r JOIN ChatName2Id ch ON ch.rowid=r.chat_id WHERE r.message_svr_id=? AND ch.user_name=?', (sid, peer)).fetchall()
                if len(same) == 1:
                    existing = dict(same[0])
                    expected = dict(info)
                    expected.update(message_id=existing['message_id'], chat_id=existing['chat_id'], sender_id=existing['sender_id'], message_local_id=local_ids[sid])
                    actor = c.execute('SELECT user_name FROM SenderName2Id WHERE rowid=?', (existing['sender_id'],)).fetchone()
                    details = [dict(r) for r in c.execute('SELECT * FROM MessageResourceDetail WHERE message_id=? ORDER BY resource_id', (existing['message_id'],))]
                    clean = lambda d: {k:v for k,v in d.items() if k not in ('resource_id','message_id')}
                    if expected != existing or not actor or actor[0] != sender or [clean(d) for d in details] != [clean(d) for d in resource['details']]:
                        raise ValueError('voice resource metadata collision')
                    continue
            if any(not row[0] or row[0] == peer for row in existing_chats):
                raise ValueError('existing resource identity collision')
            info.pop('message_id')
            info.update(chat_id=_mapping(c, 'ChatName2Id', peer, update_time=info['message_create_time']),
                        sender_id=_mapping(c, 'SenderName2Id', sender, update_time=info['message_create_time']),
                        message_local_id=local_ids[sid])
            new_id = _insert(c, 'MessageResourceInfo', info)
            for detail in resource['details']:
                detail = dict(detail)
                detail.pop('resource_id')
                detail['message_id'] = new_id
                _insert(c, 'MessageResourceDetail', detail)
        c.commit()
    with write_connect(output / RELS[2]) as c:
        c.row_factory = sqlite3.Row
        existing = c.execute('SELECT * FROM SessionTable WHERE username=?', (peer,)).fetchone()
        if data['messages'] and (added_ids or not (voice_mode or attachment_mode)):
            latest = max(data['messages'], key=lambda m: (m['fields']['sort_seq'], m['fields']['local_id']))
            # Do not replace a newer existing session preview with older history.
            if existing is None or (existing['last_timestamp'] or 0) <= latest['fields']['create_time']:
                session = dict(data['session'])
                actor = target_contacts[latest['sender']]
                nick = actor.get('remark') or actor.get('nick_name') or latest['sender']
                session.update(username=peer, unread_count=0, unread_first_msg_srv_id=0,
                               unread_first_pat_msg_local_id=0, unread_first_pat_msg_sort_seq=0,
                               is_hidden=0, status=0, last_timestamp=latest['fields']['create_time'],
                               last_msg_locald_id=local_ids[latest['fields']['server_id']],
                               last_msg_type=latest['fields']['local_type'] & 0xffffffff,
                               last_msg_sub_type=latest['fields']['local_type'] >> 32,
                               last_msg_sender=latest['sender'], last_sender_display_name=nick,
                               draft=existing['draft'] if existing else '')
                c.execute('INSERT INTO SessionTable (' + ','.join(ident(k) for k in session) + ') VALUES ('
                          + ','.join('?' for _ in session) + ') ON CONFLICT(username) DO UPDATE SET '
                          + ','.join(ident(k) + '=excluded.' + ident(k) for k in session if k != 'username'), tuple(session.values()))
            c.execute('UPDATE SessionTable SET is_hidden=0 WHERE username=?', (peer,))
            _mapping(c, 'Name2Id', peer)
            c.execute('DELETE FROM SessionDeleteTable WHERE username=?', (peer,))
        c.commit()
    voice_counts = {}
    if voice_mode:
        from .voice import restore
        voice_counts = restore(output, peer, data['messages'], local_ids, added_ids, audios)
    for rel in write_rels:
        with connect(output / rel) as c:
            if [tuple(r) for r in c.execute('PRAGMA integrity_check')] != [('ok',)]:
                raise ValueError('candidate database integrity check failed')
    private_json(output / 'direction-profile.json', {'version': 1, 'direction_profile': target_profile})
    return {'table': table, 'added': len(added_ids), 'skipped': skipped,
            'timestamp_variants_preserved': timestamp_variants,
            'source_direction_profile': source_profile, 'target_direction_profile': target_profile,
            'own_sender_id': own_id, 'peer_sender_id': peer_id, 'attachments_restored': attachment_mode,
            **attachment_counts, **({'assets':attachment_plan, 'attachment_peer':peer, 'attachment_resources_repaired':attachment_resource_repaired} if attachment_mode else {}),
            **voice_counts, 'changed_databases': [r for r in write_rels if digest(output / r) != digest(target_root / r)]}
