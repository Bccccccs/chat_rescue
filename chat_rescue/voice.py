"""Conservative offline adapter for the observed Mac VoiceInfo / SILK layout.

No decoding or transcoding. Unknown schemas/states fail closed. Import workflow
locally to keep the basic (non-voice) workflow independent of this adapter.
"""
import hashlib
from pathlib import Path
import re
from contextlib import ExitStack

MEDIA = 'message/media_0.db'
CAPABILITY = 'mac-voice-silk-v1'
MAX_AUDIO = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
HEADER = b'\x02#!SILK_V3'
VOICE_COLUMNS = [('chat_name_id', 'INTEGER'), ('create_time', 'INTEGER'),
                 ('local_id', 'INTEGER'), ('svr_id', 'INTEGER'),
                 ('voice_data', 'BLOB'), ('data_index', 'TEXT')]


class MissingVoiceDatabase(ValueError):
    """No supported existing media DB; never create a synthetic destination."""


class MissingVoiceRoute(ValueError):
    """No unique original association, distinct from conflicting evidence."""


def is_media_rel(value):
    return isinstance(value,str) and re.fullmatch(r'message/media_(0|[1-9][0-9]*)\.db',value) is not None


def media_rels(root, *, allow_missing=False):
    """Inventory existing numbered media files; never synthesize a destination."""
    directory = Path(root) / 'message'
    if directory.is_symlink() or directory.is_junction():
        raise ValueError('语音媒体目录不能使用链接')
    names = []
    for path in directory.glob('media_*'):
        if re.fullmatch(r'media_(0|[1-9][0-9]*)\.db', path.name):
            if path.is_symlink() or path.is_junction() or not path.is_file():
                raise ValueError('语音媒体分片必须为普通文件')
            names.append(path.name)
        elif path.name.endswith('.db'):
            raise ValueError('语音媒体分片编号不规范')
        elif (match := re.fullmatch(r'(media_[0-9]+\.db)-(wal|shm)', path.name)):
            if path.is_symlink() or path.is_junction() or not path.is_file() or not (directory / match[1]).is_file():
                raise ValueError('语音媒体分片存在未知或孤立 sidecar：'+path.name+'；请保留文件并核对对应数据库，不要删除 WAL/SHM')
        elif re.fullmatch(r'media_(0|[1-9][0-9]*)\.db-(first|incremental|last)\.material', path.name):
            # Tencent WCDB metadata backups, not live SQLite DB/WAL/SHM.
            # https://github.com/Tencent/wcdb/wiki/C---损坏、备份、修复
            import platform
            if platform.system()!='Windows' or path.is_symlink() or path.is_junction() or not path.is_file():
                raise ValueError('语音媒体备份路径不受支持：'+path.name)
        elif '.db-' in path.name:
            raise ValueError('语音媒体分片存在未知 sidecar：'+path.name+'；请保留文件并核对实际格式')
    if not names and not allow_missing:
        raise MissingVoiceDatabase('所选账号未发现已有 message/media_N.db 语音库；请核对账号目录，或关闭“同时恢复原语音”后继续消息恢复；不要新建空库')
    return tuple('message/' + name for name in sorted(names, key=lambda n: int(n[6:-3])))


def single_media(root):
    """Validated inventory: Mac remains single-media, Windows uses all shards."""
    import platform
    rels = media_rels(root,allow_missing=platform.system()=='Windows')
    if platform.system() == 'Windows':
        return rels
    paths = list((Path(root) / 'message').glob('media_*.db'))
    if len(paths) != 1 or paths[0].name != 'media_0.db' or paths[0].is_symlink():
        raise ValueError('语音模式仅接受单个已有 media_0.db；不支持额外媒体分片')
    for path in (Path(root)/'message').glob('media_*.db-*'):
        if path.name not in ('media_0.db-wal','media_0.db-shm') or path.is_symlink():
            raise ValueError('语音媒体分片存在未知或孤立 sidecar，拒绝继续')
    return rels


def locate(connections, peer, row, local_id, *, check_audio=True):
    matches = [(rel, found) for rel, c in connections.items()
               if (found := lookup(c, peer, row['server_id'], local_id, row['create_time'],check_audio=check_audio)) is not None]
    if len(matches) > 1:
        raise ValueError('语音跨媒体分片匹配多行，拒绝猜测')
    return matches[0] if matches else (None, None)


def target_routing(root, peer, *, allow_unavailable=False):
    """Preserve exact target associations before clearing a candidate chat."""
    from .workflow import connect
    routes = {}
    for rel in single_media(root):
        with connect(Path(root)/rel) as c:
            schema(c)
            names = c.execute('SELECT rowid FROM Name2Id WHERE user_name=?', (peer,)).fetchall()
            if len(names) > 1:
                raise ValueError('语音会话身份映射不唯一')
            if not names:
                continue
            for row in c.execute('SELECT * FROM VoiceInfo WHERE chat_name_id=?', (names[0][0],)):
                if (row['data_index'] != '0' or any(type(row[key]) is not int or row[key] <= 0
                        for key in ('svr_id', 'local_id', 'create_time'))):
                    raise ValueError('目标语音关联结构尚未适配')
                if row['voice_data'] not in (None, b'') and not allow_unavailable:
                    audio_check(row['voice_data'])
                if row['svr_id'] in routes:
                    raise ValueError('目标语音跨媒体分片关联不唯一')
                routes[row['svr_id']] = {'path': rel, 'create_time': row['create_time'], 'peer': peer}
    return routes


def destination(connections, peer, row, routes):
    if len(connections) == 1:
        return next(iter(connections))
    evidence = (routes or {}).get(row['server_id'])
    if not evidence:
        raise MissingVoiceRoute('语音缺少唯一目标媒体分片的原有关联证据，不能猜测或新建媒体库')
    if (evidence.get('path') not in connections or evidence.get('peer') != peer
            or evidence.get('create_time') != row['create_time']):
        raise ValueError('语音缺少唯一目标媒体分片的原有关联证据，拒绝按编号或时间猜测')
    return evidence['path']


def schema(c):
    import sqlite3
    for table, columns in [('VoiceInfo', VOICE_COLUMNS), ('Name2Id', [('user_name', 'TEXT')])]:
        try:
            rows = c.execute('PRAGMA table_info(' + table + ')').fetchall()
        except sqlite3.DatabaseError:
            raise ValueError('语音媒体库损坏或结构尚未适配') from None
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
    import platform
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_AUDIO:
        raise ValueError('语音缺失、过大或音频格式尚未适配')
    if platform.system() == 'Windows':
        from .media_containers import audio_format
        audio_format(data)
    elif len(data) <= len(HEADER) or not data.startswith(HEADER):
        raise ValueError('语音缺失、过大或音频格式尚未适配')
    return hashlib.sha256(data).hexdigest()


def lookup(c, chat, sid, local_id, created, *, check_audio=True):
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
    if row['voice_data'] not in (None, b'') and check_audio:
        audio_check(row['voice_data'])
    return row


def collect(root, peer, messages, output, allow_missing=False, *, extended=False):
    from .workflow import connect
    entries, total = [], 0
    with ExitStack() as stack:
        connections = {rel: stack.enter_context(connect(Path(root)/rel)) for rel in single_media(root)}
        for c in connections.values():
            schema(c)
        for m in messages:
            row = m['fields']
            if row['local_type'] != 34:
                continue
            message_state(row)
            _, found = locate(connections, peer, row, row['local_id'],check_audio=not extended)
            audio = found['voice_data'] if found else None
            entry = {'server_id': row['server_id'], 'local_id': row['local_id'],
                     'create_time': row['create_time'], 'data_index': '0', 'audio': None}
            if audio:
                from .media_containers import UnsupportedMediaFormat
                try:audio_check(audio)
                except UnsupportedMediaFormat:
                    if not extended:raise
                    entry['skip_reason']='voice-source-unavailable'
                    entries.append(entry);continue
                if not extended and (not audio.startswith(HEADER) or len(audio)<=len(HEADER)):
                    raise ValueError('Mac/legacy 语音仍仅支持腾讯 SILK v3')
                total += len(audio)
                if total > MAX_TOTAL:
                    raise ValueError('语音 bundle 超过本版体积限制')
                import platform
                from .media_containers import audio_format
                extension = audio_format(audio) if extended and platform.system() == 'Windows' else 'silk'
                path = 'voice/' + str(len(entries) + 1).zfill(6) + '.' + extension
                p = Path(output) / path
                p.parent.mkdir(mode=0o700, exist_ok=True)
                with p.open('xb') as f:
                    f.write(audio)
                p.chmod(0o600)
                entry['audio'] = {'path': path, 'size': len(audio), 'sha256': audio_check(audio)}
            elif extended:
                entry['skip_reason']='voice-source-unavailable'
            elif not allow_missing:
                raise ValueError('源语音音频缺失，不能导出完整语音；双向模式可检查另一侧原件')
            entries.append(entry)
    return entries


def read_entries(bundle, data):
    """Validate every entry before creating any candidate, including path parents."""
    import platform
    from .media import WINDOWS_CONTAINERS
    extended = platform.system()=='Windows' and data.get('version')==5 and WINDOWS_CONTAINERS in data.get('capabilities',[])
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
        if 'skip_reason' in entry:
            from .media import WINDOWS_SKIP_UNAVAILABLE
            if (not extended or WINDOWS_SKIP_UNAVAILABLE not in data.get('capabilities',[])
                    or entry['skip_reason']!='voice-source-unavailable' or descriptor is not None):
                raise ValueError('语音跳过原因或能力无效')
        audio = None
        if descriptor is not None:
            name, size = descriptor['path'], descriptor['size']
            import platform
            formats = 'silk|wav|amr' if extended else 'silk'
            if (not isinstance(name, str) or not re.fullmatch(r'voice/[0-9]{6}\.(' + formats + ')', name)
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
            if not extended and (not audio.startswith(HEADER) or len(audio)<=len(HEADER)):
                raise ValueError('Mac/legacy 语音仍仅支持腾讯 SILK v3')
            if extended:
                from .media_containers import audio_format
                if path.suffix != '.' + audio_format(audio):
                    raise ValueError('语音扩展名与容器不符')
        entries[sid] = audio
    if set(entries) != set(rows):
        raise ValueError('语音清单与消息不一一对应')
    return entries


def restore(root, peer, messages, local_ids, added_ids, audios, *, routes=None, extended=False):
    from .workflow import write_connect, _mapping
    counts = dict(voice_added=0, voice_repaired=0, voice_skipped=0, voice_blocked=0,
                  voice_source_unavailable=0,voice_target_unavailable=0)
    with ExitStack() as stack:
        import sqlite3
        connections = {rel: stack.enter_context(write_connect(Path(root)/rel)) for rel in single_media(root)}
        for c in connections.values():
            c.row_factory = sqlite3.Row
            schema(c)
        for m in messages:
            row = m['fields']
            if row['local_type'] != 34:
                continue
            sid, lid = row['server_id'], local_ids[row['server_id']]
            if extended and audios[sid] is None:
                counts['voice_source_unavailable']+=1;counts['voice_blocked']+=1;continue
            if extended and not connections:
                counts['voice_target_unavailable']+=1;counts['voice_blocked']+=1;continue
            rel, found = locate(connections, peer, row, lid,check_audio=not extended)
            if rel is None:
                try:rel = destination(connections, peer, row, routes)
                except MissingVoiceRoute:
                    if not extended:raise
                    counts['voice_target_unavailable']+=1;counts['voice_blocked']+=1;continue
            c = connections[rel]
            old = found['voice_data'] if found else None
            audio = audios[sid]
            if not extended and any(value and not value.startswith(HEADER) for value in (old,audio)):
                raise ValueError('Mac/legacy 语音仍仅支持腾讯 SILK v3')
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
