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
from .shards import message_rels, database_rels, mutable_rels, check_paths

RELS = ('message/message_0.db', 'message/message_resource.db', 'session/session.db')
SUPPORTED = {1, 42, (6 << 32) | 49}
LEGACY_PROFILE = 'legacy-v1'
WINDOWS_TEXT_PROFILE = 'windows-4.2.0.12-text'
WINDOWS_STRUCTURED_PROFILE = 'windows-4.2.0.12-structured'
WINDOWS_PROFILES = (WINDOWS_TEXT_PROFILE, WINDOWS_STRUCTURED_PROFILE)


def direction_profile(value):
    if value not in (LEGACY_PROFILE, *WINDOWS_PROFILES):
        raise ValueError('unsupported message direction profile')
    return value


def plain_profile(root):
    for name in ('export-report.json', 'direction-profile.json'):
        path = Path(root) / name
        if path.exists():
            return direction_profile(json.loads(path.read_text(encoding='utf-8')).get('direction_profile', LEGACY_PROFILE))
    return LEGACY_PROFILE


def outgoing_flag(profile):
    return 3 if direction_profile(profile) in WINDOWS_PROFILES else 1


def check_profile_type(local_type, *profiles):
    if type(local_type) is not int or not 0 < local_type < (1 << 63):
        raise ValueError('invalid message type')
    if LEGACY_PROFILE in profiles and local_type not in SUPPORTED:
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
    # Compatibility for callers; every numbered shard is now part of the scope.
    return message_rels(root)


def message_tables(c):
    return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if r[0].startswith('Msg_')]


def message_hits(root, server_ids):
    """Batch lookup across ALL conversations/shards; never trust a shard-local ID."""
    hits = {}
    ids = list(server_ids)
    for rel in message_rels(root):
        with connect(Path(root) / rel) as c:
            tables = message_tables(c)
            if not tables:
                continue
            names = {r['rowid']: r['user_name'] for r in c.execute('SELECT rowid,user_name FROM Name2Id')}
            for table in tables:
                for start in range(0, len(ids), 500):
                    batch = ids[start:start + 500]
                    for raw in c.execute('SELECT * FROM ' + ident(table) + ' WHERE server_id IN (' +
                                         ','.join('?' for _ in batch) + ')', batch):
                        row = dict(raw)
                        hits.setdefault(row['server_id'], []).append(
                            (rel, table, row, names.get(row['real_sender_id'])))
    return hits


def save_chat(plain_root, owner, peer, output):
    os.umask(0o077)
    root, output = Path(plain_root), Path(output)
    if owner == peer or '@' in owner or '@' in peer:
        raise ValueError('only two different personal WeChat identities are supported')
    shards = message_rels(root)
    check_paths(root, database_rels(root))
    report_path = root / 'export-report.json'
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding='utf-8'))
        account = report.get('account_directory', '')
        if account != owner and not account.startswith(owner + '_'):
            raise ValueError('owner 与导出账号目录不匹配')
        if set(report.get('exported', {})) != set(database_rels(root)):
            raise ValueError('导出数据库分片清单 inventory 不完整或改变')
        if any(digest(root / rel) != value for rel, value in report['exported'].items()):
            raise ValueError('导出数据库哈希改变')
    profile = plain_profile(root)
    with connect(root / 'contact/contact.db') as c:
        participants = [dict(r) for r in c.execute('SELECT * FROM contact WHERE username IN (?,?)', (owner, peer))]
        if {r['username'] for r in participants} != {owner, peer}:
            raise ValueError('owner/peer must be exact usernames in the contact database')
    table = table_for(peer)
    schema, indexes, columns = None, [], None
    messages, unique, source_shards = [], {}, []
    for rel in shards:
        with connect(root / rel) as c:
            found = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not found:
                continue
            current_columns = [tuple(r) for r in c.execute('PRAGMA table_info(' + ident(table) + ')')]
            current_indexes = [r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name", (table,))]
            if schema is None:
                schema, indexes, columns = found[0], current_indexes, current_columns
            elif columns != current_columns or schema != found[0] or indexes != current_indexes:
                raise ValueError('incompatible message schema across source shards')
            source_shards.append(rel)
            names = {r['rowid']: r['user_name'] for r in c.execute('SELECT rowid,user_name FROM Name2Id')}
            for raw in c.execute('SELECT * FROM ' + ident(table) + ' ORDER BY sort_seq,local_id'):
                row = dict(raw)
                sender = names.get(row['real_sender_id'])
                if sender not in (owner, peer):
                    raise ValueError('message sender is outside the selected two parties')
                check_profile_type(row['local_type'], profile)
                sid = row['server_id']
                if not isinstance(sid, int) or sid <= 0:
                    raise ValueError('a real positive server_id is required; no ID is fabricated')
                canonical = {k: v for k, v in row.items() if k not in ('local_id', 'real_sender_id')}
                canonical['packed_info_data'] = flip_packed(
                    row['packed_info_data'], row['real_sender_id'], 1, sender == peer,
                    source_outgoing=outgoing_flag(profile), target_outgoing=outgoing_flag(profile))
                canonical['sender'] = sender
                if sid in unique:
                    if unique[sid] != canonical:
                        raise ValueError('server ID collision across source shards')
                    continue
                unique[sid] = canonical
                messages.append({'sender': sender, 'fields': row, 'source_shard': rel})
    if not messages:
        raise ValueError('selected conversation has no local messages')
    messages.sort(key=lambda m: (m['fields']['sort_seq'], m['fields']['create_time'], m['fields']['server_id']))
    resources = []
    with connect(root / RELS[1]) as c:
        for m in messages:
            for row in c.execute('SELECT * FROM MessageResourceInfo WHERE message_svr_id=?', (m['fields']['server_id'],)):
                chat = c.execute('SELECT user_name FROM ChatName2Id WHERE rowid=?', (row['chat_id'],)).fetchone()
                if chat is None:
                    raise ValueError('source resource chat identity missing')
                if chat[0] != peer:
                    continue
                sender = c.execute('SELECT user_name FROM SenderName2Id WHERE rowid=?', (row['sender_id'],)).fetchone()
                if sender is None or sender[0] != m['sender'] or row['message_local_type'] != m['fields']['local_type']:
                    raise ValueError('source resource sender/type conflicts with message')
                resources.append({'info': dict(row), 'details': [dict(x) for x in c.execute(
                    'SELECT * FROM MessageResourceDetail WHERE message_id=?', (row['message_id'],))]})
    with connect(root / RELS[2]) as c:
        session = c.execute('SELECT * FROM SessionTable WHERE username=?', (peer,)).fetchone()
        if session is None:
            raise ValueError('source conversation session metadata missing')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    payload = {'version': 1, 'owner': owner, 'peer': peer, 'source_table': table, 'direction_profile': profile,
               'schema': schema, 'indexes': indexes, 'participants': participants, 'source_shards': source_shards,
               'messages': messages, 'resources': resources, 'session': dict(session)}
    private_json(output / 'conversation.json', pack(payload))
    private_json(output / 'manifest.json', {'version': 1, 'sha256': digest(output / 'conversation.json'),
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


def prepare_plain_restore(bundle, target_root, target_owner, output):
    os.umask(0o077)
    bundle, target_root, output = Path(bundle), Path(target_root), Path(output)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or digest(bundle / 'conversation.json') != manifest.get('sha256'):
        raise ValueError('bundle version/hash mismatch')
    data = unpack(json.loads((bundle / 'conversation.json').read_text(encoding='utf-8')))
    if data.get('version') != 1 or target_owner != data['peer']:
        raise ValueError('target owner must be the source conversation peer')
    source_profile = direction_profile(data.get('direction_profile', LEGACY_PROFILE))
    target_profile = plain_profile(target_root)
    shards = message_rels(target_root)
    check_paths(target_root, database_rels(target_root))
    peer = data['owner']
    if peer == target_owner or '@' in peer or '@' in target_owner:
        raise ValueError('only two different personal WeChat identities are supported')
    server_ids = [m['fields']['server_id'] for m in data['messages']]
    if (len(server_ids) != manifest.get('message_count') or not server_ids
            or any(type(sid) is not int or sid <= 0 for sid in server_ids)
            or len(set(server_ids)) != len(server_ids)):
        raise ValueError('invalid or duplicate server IDs in bundle')
    hits_by_id = message_hits(target_root, server_ids)
    containing, max_local_id = [], 0
    table = table_for(peer)
    for rel in shards:
        with connect(target_root / rel) as c:
            if table in message_tables(c):
                containing.append(rel)
                max_local_id = max(max_local_id, c.execute('SELECT coalesce(max(local_id),0) FROM ' + ident(table)).fetchone()[0])
    destination = (containing or list(shards))[-1]
    with connect(target_root / 'contact/contact.db') as c:
        target_contacts = {r['username']: dict(r) for r in c.execute('SELECT * FROM contact WHERE username IN (?,?)', (target_owner, peer))}
        users = set(target_contacts)
        if users != {target_owner, peer}:
            raise ValueError('log into target account and sync/open the peer contact first')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    for rel in database_rels(target_root):
        (output / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target_root / rel, output / rel)
    table = table_for(peer)
    local_ids, added_ids, skipped, timestamp_variants = {}, set(), 0, 0
    with write_connect(output / destination) as c:
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
            check_profile_type(row['local_type'], source_profile, target_profile)
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
            # Nontext playback/download states are not text sent/read flags.
            # Like the upstream Mac media adapter, retain their original state.
            if target_profile != WINDOWS_STRUCTURED_PROFILE or row['local_type'] == 1:
                row['status'] = 2 if target_profile in WINDOWS_PROFILES and outgoing else 3
            hits = hits_by_id.get(row['server_id'], [])
            if hits:
                if any(hit[1] != table for hit in hits):
                    raise ValueError('server ID collision in another conversation')
                time_variant = False
                for _, _, existing, existing_sender in hits:
                    if existing_sender != m['sender'] or any(existing[k] != row[k] for k in ['message_content', 'compress_content', 'local_type']):
                        raise ValueError('server ID collision with different message contents/identity')
                    # Validate using this hit's own shard-local sender mapping.
                    flip_packed(existing['packed_info_data'], existing['real_sender_id'], 1, not outgoing,
                                source_outgoing=outgoing_flag(target_profile), target_outgoing=outgoing_flag(target_profile))
                    if existing['create_time'] != row['create_time']:
                        delta = existing['create_time'] - row['create_time']
                        if (row['local_type'] != 1 or source_profile not in WINDOWS_PROFILES or target_profile not in WINDOWS_PROFILES
                                or abs(delta) != 1 or existing['sort_seq'] - row['sort_seq'] != delta * 1000):
                            raise ValueError('server ID collision with different message contents/identity')
                        time_variant = True
                timestamp_variants += int(time_variant)
                chosen = next((hit for hit in hits if hit[0] == destination), hits[-1])
                local_ids[row['server_id']] = chosen[2]['local_id']
                skipped += 1
                continue
            max_local_id += 1
            row['local_id'] = max_local_id
            local_ids[row['server_id']] = _insert(c, table, row)
            added_ids.add(row['server_id'])
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='DeleteInfo'").fetchone():
            c.execute('DELETE FROM DeleteInfo WHERE chat_name_id=? OR delete_table_name=?', (peer_id, table))
        c.commit()
    # Tombstones are shard-local too. Clear only this conversation's markers.
    for rel in shards:
        if rel == destination:
            continue
        with connect(output / rel) as c:
            has_delete = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='DeleteInfo'").fetchone()
            mapping = c.execute('SELECT rowid FROM Name2Id WHERE user_name=?', (peer,)).fetchone() if has_delete else None
            marked = has_delete and c.execute('SELECT 1 FROM DeleteInfo WHERE chat_name_id=? OR delete_table_name=?',
                                             (mapping[0] if mapping else None, table)).fetchone()
        if marked:
            with write_connect(output / rel) as c:
                c.execute('DELETE FROM DeleteInfo WHERE chat_name_id=? OR delete_table_name=?',
                          (mapping[0] if mapping else None, table))
    with write_connect(output / RELS[1]) as c:
        senders = {m['fields']['server_id']: m['sender'] for m in data['messages']}
        for resource in data['resources']:
            info = dict(resource['info'])
            sid = info['message_svr_id']
            if sid not in added_ids:
                continue
            sender = senders[sid]
            existing_chats = c.execute(
                'SELECT chats.user_name FROM MessageResourceInfo AS resource '
                'LEFT JOIN ChatName2Id AS chats ON chats.rowid=resource.chat_id '
                'WHERE resource.message_svr_id=?', (sid,)).fetchall()
            # Resource caches can share a server ID across conversations. Keep
            # foreign caches intact, but reject ambiguous or same-chat entries.
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
        if data['messages']:
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
    for rel in mutable_rels(output):
        with connect(output / rel) as c:
            if [tuple(r) for r in c.execute('PRAGMA integrity_check')] != [('ok',)]:
                raise ValueError('candidate database integrity check failed')
    private_json(output / 'direction-profile.json', {'version': 1, 'direction_profile': target_profile})
    return {'table': table, 'added': len(added_ids), 'skipped': skipped,
            'timestamp_variants_preserved': timestamp_variants,
            'source_direction_profile': source_profile, 'target_direction_profile': target_profile,
            'source_message_shards': data.get('source_shards', ['message/message_0.db']),
            'target_message_shard': destination, 'target_message_shards': list(shards),
            'own_sender_id': own_id, 'peer_sender_id': peer_id, 'attachments_restored': False}
