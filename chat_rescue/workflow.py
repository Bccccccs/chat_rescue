"""A narrow, identity-based two-party WeChat recovery workflow."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

RELS = ('message/message_0.db', 'message/message_resource.db', 'session/session.db')
SUPPORTED = {1, 42, (6 << 32) | 49}


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
        os.chmod(path, 0o600)
        json.dump(value, f, ensure_ascii=False, indent=2)


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


def flip_packed(data, old_sender, new_sender, outgoing):
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
            wanted = 1 if outgoing else 2
            if wire != 0 or value not in (1, 2) or value == wanted:
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
        raise ValueError('v0.1 supports one message_0.db shard only')


def save_chat(plain_root, owner, peer, output):
    os.umask(0o077)
    root, output = Path(plain_root), Path(output)
    if owner == peer or '@' in owner or '@' in peer:
        raise ValueError('only two different personal WeChat identities are supported')
    _single_shard(root)
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
            if row['local_type'] not in SUPPORTED:
                raise ValueError('unsupported message type: ' + str(row['local_type']))
            if not isinstance(row['server_id'], int) or row['server_id'] <= 0:
                raise ValueError('a real positive server_id is required; no ID is fabricated')
            # Validate the observed opposite-account direction structure now.
            flip_packed(row['packed_info_data'], row['real_sender_id'], 1, sender == peer)
            messages.append({'sender': sender, 'fields': row})
        if not messages or len({m['fields']['server_id'] for m in messages}) != len(messages):
            raise ValueError('conversation is empty or has duplicate server IDs')
    resources = []
    with connect(root / RELS[1]) as c:
        for m in messages:
            for row in c.execute('SELECT * FROM MessageResourceInfo WHERE message_svr_id=?', (m['fields']['server_id'],)):
                resources.append({'info': dict(row), 'details': [dict(x) for x in c.execute(
                    'SELECT * FROM MessageResourceDetail WHERE message_id=?', (row['message_id'],))]})
    with connect(root / RELS[2]) as c:
        session = c.execute('SELECT * FROM SessionTable WHERE username=?', (peer,)).fetchone()
        if session is None:
            raise ValueError('source conversation session metadata missing')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    payload = {'version': 1, 'owner': owner, 'peer': peer, 'source_table': table,
               'schema': schema[0], 'indexes': indexes, 'participants': participants,
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
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if manifest.get('version') != 1 or digest(bundle / 'conversation.json') != manifest.get('sha256'):
        raise ValueError('bundle version/hash mismatch')
    data = unpack(json.loads((bundle / 'conversation.json').read_text()))
    if data.get('version') != 1 or target_owner != data['peer']:
        raise ValueError('target owner must be the source conversation peer')
    _single_shard(target_root)
    peer = data['owner']
    with connect(target_root / 'contact/contact.db') as c:
        target_contacts = {r['username']: dict(r) for r in c.execute('SELECT * FROM contact WHERE username IN (?,?)', (target_owner, peer))}
        users = set(target_contacts)
        if users != {target_owner, peer}:
            raise ValueError('log into target account and sync/open the peer contact first')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    for rel in (*RELS, 'contact/contact.db'):
        (output / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target_root / rel, output / rel)
    table = table_for(peer)
    local_ids, added_ids, skipped = {}, set(), 0
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
            if row['local_type'] not in SUPPORTED or row['server_id'] <= 0 or m['sender'] not in (target_owner, peer):
                raise ValueError('invalid message in bundle')
            outgoing = m['sender'] == target_owner
            new_sender = own_id if outgoing else peer_id
            old_sender = row['real_sender_id']
            row.pop('local_id')
            row['real_sender_id'] = new_sender
            row['packed_info_data'] = flip_packed(row['packed_info_data'], old_sender, new_sender, outgoing)
            row['status'] = 3
            hits = []
            for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"):
                hits += [(name, dict(r)) for r in c.execute('SELECT * FROM ' + ident(name) + ' WHERE server_id=?', (row['server_id'],))]
            if hits:
                if len(hits) != 1 or hits[0][0] != table:
                    raise ValueError('server ID collision in another conversation')
                existing = hits[0][1]
                if any(existing[k] != row[k] for k in ['message_content', 'compress_content', 'local_type', 'create_time', 'real_sender_id']):
                    raise ValueError('server ID collision with different message contents/identity')
                local_ids[row['server_id']] = existing['local_id']
                skipped += 1
                continue
            local_ids[row['server_id']] = _insert(c, table, row)
            added_ids.add(row['server_id'])
        c.execute('DELETE FROM DeleteInfo WHERE chat_name_id=? OR delete_table_name=?', (peer_id, table))
        c.commit()
    with write_connect(output / RELS[1]) as c:
        for resource in data['resources']:
            info = dict(resource['info'])
            sid = info['message_svr_id']
            if sid not in added_ids:
                continue
            sender = next(m['sender'] for m in data['messages'] if m['fields']['server_id'] == sid)
            if c.execute('SELECT 1 FROM MessageResourceInfo WHERE message_svr_id=?', (sid,)).fetchone():
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
    for rel in RELS:
        with connect(output / rel) as c:
            if [tuple(r) for r in c.execute('PRAGMA integrity_check')] != [('ok',)]:
                raise ValueError('candidate database integrity check failed')
    return {'table': table, 'added': len(added_ids), 'skipped': skipped,
            'own_sender_id': own_id, 'peer_sender_id': peer_id, 'attachments_restored': False}
