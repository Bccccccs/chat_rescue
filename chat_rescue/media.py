"""Version negotiation for explicit Windows media experiments.

Layout checks and byte comparisons are offline evidence, never client playback
evidence. Version 5 deliberately cannot be read by the old Windows desktop.
"""
WINDOWS_MEDIA = 'windows-media-layout-v1'
WINDOWS_VOICE = 'windows-voice-silk-layout-v1'
WINDOWS_ATTACHMENTS = 'windows-attachments-layout-v1'


def windows_capabilities(voice=False, attachments=False):
    if not (voice or attachments):
        raise ValueError('Windows 媒体模式必须显式选择语音或附件')
    return [WINDOWS_MEDIA] + ([WINDOWS_VOICE] if voice else []) + ([WINDOWS_ATTACHMENTS] if attachments else [])


def windows_options(value):
    caps = value.get('capabilities', [])
    voice, attachments = WINDOWS_VOICE in caps, WINDOWS_ATTACHMENTS in caps
    if value.get('version') != 5 or caps != windows_capabilities(voice, attachments):
        raise ValueError('Windows 媒体能力或版本不受支持')
    return {'include_voice': voice, 'include_attachments': attachments}


def format_for(profile, voice=False, attachments=False):
    from .workflow import WINDOWS_PROFILES
    from .voice import CAPABILITY as VOICE
    from .attachments import CAPABILITY as ATTACHMENTS
    if profile in WINDOWS_PROFILES and (voice or attachments):
        return 5, windows_capabilities(voice, attachments)
    return (3 if attachments else 2 if voice else 1,
            ([VOICE] if voice else []) + ([ATTACHMENTS] if attachments else []))


def check_voice_layout(root):
    """Accept one explicit layout; matching two unknown schemas is insufficient."""
    from pathlib import Path
    import re
    from .workflow import connect, ident
    from .voice import MEDIA, VOICE_COLUMNS, schema, single_media
    single_media(root)
    with connect(Path(root)/MEDIA) as c:
        schema(c)
        tables={row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables!={'VoiceInfo','Name2Id'}:
            raise ValueError('Windows 语音库存在未适配表，拒绝猜测关联')
        for table,columns in (('VoiceInfo',VOICE_COLUMNS),('Name2Id',[('user_name','TEXT')])):
            definition=c.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone()[0]
            if re.search(r'\b(CHECK|COLLATE|GENERATED|REFERENCES|WITHOUT|STRICT|UNIQUE)\b',definition,re.I):
                raise ValueError('Windows 语音库存在未适配约束')
            expected=[(name,kind,0,"'0'" if name=='data_index' else None,1 if table=='Name2Id' else 0) for name,kind in columns]
            if [tuple(row)[1:] for row in c.execute('PRAGMA table_info('+table+')')]!=expected:
                raise ValueError('Windows 语音库约束或默认值尚未适配')
            if c.execute('PRAGMA foreign_key_list('+table+')').fetchone():
                raise ValueError('Windows 语音库存在未适配外键')
        indexes=[]
        for row in c.execute('PRAGMA index_list(VoiceInfo)'):
            fields=[(item[2],item[3],item[4]) for item in c.execute('PRAGMA index_xinfo('+ident(row[1])+')') if item[5]]
            indexes.append((row[2],row[4],fields))
        expected=[(1,0,[(name,0,'BINARY') for name in ('chat_name_id','create_time','local_id','data_index')]),
                  (0,0,[(name,0,'BINARY') for name in ('chat_name_id','svr_id')])]
        if sorted(indexes)!=sorted(expected):
            raise ValueError('Windows 语音库索引尚未适配')
