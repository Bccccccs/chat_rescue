"""Conservative offline adapter for the observed Mac VoiceInfo / SILK layout.

No decoding or transcoding. Unknown schemas/states fail closed. Import workflow
locally to keep the basic (non-voice) workflow independent of this adapter.
"""
import hashlib
from pathlib import Path
import re

MEDIA = 'message/media_0.db'
CAPABILITY = 'mac-voice-silk-v1'
MAX_AUDIO = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
HEADER = b'\x02#!SILK_V3'
VOICE_COLUMNS = [('chat_name_id', 'INTEGER'), ('create_time', 'INTEGER'),
                 ('local_id', 'INTEGER'), ('svr_id', 'INTEGER'),
                 ('voice_data', 'BLOB'), ('data_index', 'TEXT')]


def single_media(root):
    paths = list((Path(root) / 'message').glob('media_*.db'))
    if len(paths) != 1 or paths[0].name != 'media_0.db' or paths[0].is_symlink():
        raise ValueError('语音模式仅接受单个已有 media_0.db；不支持额外媒体分片')
    for path in (Path(root)/'message').glob('media_*.db-*'):
        if path.name not in ('media_0.db-wal','media_0.db-shm') or path.is_symlink():
            raise ValueError('语音媒体分片存在未知或孤立 sidecar，拒绝继续')


def schema(c):
    for table, columns in [('VoiceInfo', VOICE_COLUMNS), ('Name2Id', [('user_name', 'TEXT')])]:
        rows = c.execute('PRAGMA table_info(' + table + ')').fetchall()
        if [(r[1], r[2].upper()) for r in rows] != columns:
            raise ValueError('语音媒体库结构尚未适配')
    unique = []
    for row in c.execute('PRAGMA index_list(VoiceInfo)').fetchall():
        if row[2] and not row[4]:
            from .workflow import ident
            unique.append([x[2] for x in c.execute('PRAGMA index_info(' + ident(row[1]) + ')')])
    if ['chat_name_id', 'create_time', 'local_id', 'data_index'] not in unique:
        raise ValueError('语音媒体库缺少已核验的唯一索引')
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('VoiceInfo','Name2Id')").fetchone():
        raise ValueError('语音媒体库存在未适配触发器')


def message_state(row):
    if (row['status'] != 3 or row['upload_status'] != 0
            or row['download_status'] not in (0, 1, 4, 5)):
        raise ValueError('语音状态尚未适配；保留原件，不能套用文本状态')


def audio_check(data):
    if not isinstance(data, bytes) or not len(HEADER) < len(data) <= MAX_AUDIO or not data.startswith(HEADER):
        raise ValueError('语音缺失、过大或音频格式尚未适配')
    return hashlib.sha256(data).hexdigest()


def lookup(c, chat, sid, local_id, created):
    """Scope by this media DB's own username mapping, never a foreign numeric ID."""
    names = c.execute('SELECT rowid FROM Name2Id WHERE user_name=?', (chat,)).fetchall()
    if len(names) > 1:
        raise ValueError('语音会话身份映射不唯一')
    rows = c.execute('SELECT rowid,* FROM VoiceInfo WHERE chat_name_id=? AND svr_id=?',
                     (names[0][0], sid)).fetchall() if names else []
    if len(rows) > 1:
        raise ValueError('语音匹配多行，结构尚未适配')
    if not rows:
        return None
    row = dict(rows[0])
    if row['local_id'] != local_id or row['create_time'] != created or row['data_index'] != '0':
        raise ValueError('语音本地关联或 data_index 尚未适配')
    if row['voice_data'] not in (None, b''):
        audio_check(row['voice_data'])
    return row


def collect(root, peer, messages, output, allow_missing=False):
    from .workflow import connect
    single_media(root)
    entries, total = [], 0
    with connect(Path(root) / MEDIA) as c:
        schema(c)
        for m in messages:
            row = m['fields']
            if row['local_type'] != 34:
                continue
            message_state(row)
            found = lookup(c, peer, row['server_id'], row['local_id'], row['create_time'])
            audio = found['voice_data'] if found else None
            entry = {'server_id': row['server_id'], 'local_id': row['local_id'],
                     'create_time': row['create_time'], 'data_index': '0', 'audio': None}
            if audio:
                total += len(audio)
                if total > MAX_TOTAL:
                    raise ValueError('语音 bundle 超过本版体积限制')
                path = 'voice/' + str(len(entries) + 1).zfill(6) + '.silk'
                p = Path(output) / path
                p.parent.mkdir(mode=0o700, exist_ok=True)
                with p.open('xb') as f:
                    f.write(audio)
                p.chmod(0o600)
                entry['audio'] = {'path': path, 'size': len(audio), 'sha256': audio_check(audio)}
            elif not allow_missing:
                raise ValueError('源语音音频缺失，不能导出完整语音；双向模式可检查另一侧原件')
            entries.append(entry)
    return entries


def read_entries(bundle, data):
    """Validate every entry before creating any candidate, including path parents."""
    rows = {m['fields']['server_id']: m['fields'] for m in data['messages'] if m['fields']['local_type'] == 34}
    entries, used, total = {}, set(), 0
    for entry in data.get('voices', []):
        sid = entry['server_id']
        if sid not in rows or sid in entries:
            raise ValueError('语音清单与消息不一一对应')
        row = rows[sid]
        if (entry['local_id'] != row['local_id'] or entry['create_time'] != row['create_time']
                or entry['data_index'] != '0'):
            raise ValueError('语音清单本地关联冲突')
        message_state(row)
        descriptor = entry['audio']
        audio = None
        if descriptor is not None:
            name, size = descriptor['path'], descriptor['size']
            if (not isinstance(name, str) or not re.fullmatch(r'voice/[0-9]{6}\.silk', name)
                    or name in used or type(size) is not int or not 0 < size <= MAX_AUDIO):
                raise ValueError('语音文件路径、大小或清单无效')
            used.add(name)
            path = Path(bundle) / name
            if any(p.is_symlink() for p in (Path(bundle), path.parent, path)) or not path.is_file():
                raise ValueError('语音文件不存在或使用符号链接')
            total += size
            if total > MAX_TOTAL or path.stat().st_size != size:
                raise ValueError('语音文件大小校验失败')
            with path.open('rb') as f:
                audio = f.read(MAX_AUDIO + 1)
            if len(audio) != size or audio_check(audio) != descriptor['sha256']:
                raise ValueError('语音文件哈希校验失败')
        entries[sid] = audio
    if set(entries) != set(rows):
        raise ValueError('语音清单与消息不一一对应')
    return entries


def restore(root, peer, messages, local_ids, added_ids, audios):
    from .workflow import write_connect, _mapping
    counts = dict(voice_added=0, voice_repaired=0, voice_skipped=0, voice_blocked=0)
    with write_connect(Path(root) / MEDIA) as c:
        import sqlite3
        c.row_factory = sqlite3.Row
        schema(c)
        for m in messages:
            row = m['fields']
            if row['local_type'] != 34:
                continue
            sid, lid = row['server_id'], local_ids[row['server_id']]
            found = lookup(c, peer, sid, lid, row['create_time'])
            old = found['voice_data'] if found else None
            audio = audios[sid]
            if old and audio and old != audio:
                raise ValueError('语音音频 collision：双方非空音频不同，拒绝覆盖')
            if old:
                counts['voice_skipped'] += 1
                continue
            if not audio:
                raise ValueError('双方语音音频均缺失，不能生成可应用计划')
            if found:
                c.execute('UPDATE VoiceInfo SET voice_data=? WHERE rowid=?', (audio, found['rowid']))
            else:
                chat_id = _mapping(c, 'Name2Id', peer)
                c.execute('INSERT INTO VoiceInfo(chat_name_id,create_time,local_id,svr_id,voice_data,data_index) VALUES(?,?,?,?,?,?)',
                          (chat_id, row['create_time'], lid, sid, audio, '0'))
            # Verify the actual stored bytes and associations before encryption.
            stored = lookup(c, peer, sid, lid, row['create_time'])
            if stored['voice_data'] != audio:
                raise ValueError('语音写入读回不一致')
            counts['voice_added' if sid in added_ids else 'voice_repaired'] += 1
    return counts
