"""Strict structural checks for the Windows single-conversation replacement."""
import hashlib
import json
import re
from pathlib import Path
from .workflow import connect, ident, table_for
from .shards import message_rels, check_paths, database_rels

OPTIONAL_MESSAGE_TABLES={'DeleteInfo','DeleteResInfo','HistoryAddMsgInfo','HistorySysMsgInfo','SendInfo'}


def _aux_match(left,right):
    """Deletion/history tables can be created lazily by the native client."""
    if any(shape['triggers'] for shape in (*left.values(),*right.values())):return False
    if (set(left)^set(right))-OPTIONAL_MESSAGE_TABLES:return False
    return all(left[name]==right[name] for name in set(left)&set(right))


def _normalized(sql, table):
    sql=re.sub(r'(?<!\w)'+re.escape(table)+r'(?!\w)','selected_table',sql)
    return re.sub(r'\s+',' ',sql).strip()


def table_shape(c, table):
    found=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
    if not found:return None
    sql=found[0] or ''
    definition=sql[sql.find('('):] if '(' in sql else sql
    indexes=[]
    for item in c.execute('PRAGMA index_list('+ident(table)+')'):
        fields=[tuple(row)[1:] for row in c.execute('PRAGMA index_xinfo('+ident(item['name'])+')')]
        statement=c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",(item['name'],)).fetchone()
        raw=statement[0] if statement else None
        expression=re.split(r'\bON\b',raw,maxsplit=1,flags=re.I)[1] if raw else None
        indexes.append({'unique':item['unique'],'origin':item['origin'],'partial':item['partial'],
                        'fields':fields,'expression':_normalized(expression,table) if expression else None})
    return {'columns':[tuple(row)[1:] for row in c.execute('PRAGMA table_xinfo('+ident(table)+')')],
            'definition':_normalized(definition,table),
            'indexes':sorted(indexes,key=lambda item:json.dumps(item,sort_keys=True)),
            'foreign_keys':[tuple(row) for row in c.execute('PRAGMA foreign_key_list('+ident(table)+')')],
            'triggers':[_normalized(row[0] or '',table) for row in c.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? ORDER BY name",(table,))]}


def _fingerprint(shape):
    return hashlib.sha256(json.dumps(shape,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def replacement_schema_check(source, target, source_peer, target_peer, *, candidate=False):
    """Reject structural drift; target selected tables are recreated then checked.

    Account data, local row IDs, keys, shard counts and metadata *values* are
    deliberately not equated. This check does not claim client visibility.
    """
    source,target=Path(source),Path(target)
    for root in (source,target):check_paths(root,database_rels(root))
    reference=None;source_aux=None;matched=0;source_parts=0;target_parts=0
    for rel in message_rels(source):
        with connect(source/rel) as c:
            shape=table_shape(c,table_for(source_peer))
            if shape is not None:
                if shape['triggers']:raise ValueError('源会话含未支持的触发器，拒绝生成替换候选')
                if reference is None:reference=shape
                elif shape!=reference:raise ValueError('B 各分片的会话字段或索引结构不一致：'+rel)
                source_parts+=1
            aux={row[0]:table_shape(c,row[0]) for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'Msg_%'")}
            if source_aux is None:source_aux=aux
            elif not _aux_match(source_aux,aux):raise ValueError('B 各分片辅助表结构不一致：'+rel)
            else:source_aux.update(aux)
    if reference is None:raise ValueError('B 没有可核对的单聊表结构')
    for rel in message_rels(target):
        with connect(target/rel) as c:
            aux={row[0]:table_shape(c,row[0]) for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'Msg_%'")}
            if not _aux_match(aux,source_aux):raise ValueError('A/B 消息分片辅助表结构不一致：'+rel)
            matched+=len(aux)
            shape=table_shape(c,table_for(target_peer))
            if candidate and shape is not None:
                if shape!=reference:raise ValueError('A 候选与 B 的会话字段或索引结构不一致：'+rel)
                target_parts+=1
    if candidate and not target_parts:raise ValueError('A 候选没有已核对的单聊表结构')
    for rel in ('message/message_resource.db','session/session.db','contact/contact.db'):
        shapes=[]
        for root in (source,target):
            with connect(root/rel) as c:
                shapes.append({row[0]:table_shape(c,row[0]) for row in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")})
        if shapes[0]!=shapes[1]:raise ValueError('A/B 辅助库字段或索引结构不一致：'+rel)
        if any(shape['triggers'] for shape in (*shapes[0].values(),*shapes[1].values())):
            raise ValueError('辅助库存在未支持的触发器，拒绝生成候选：'+rel)
        matched+=len(shapes[0])
    return {'state':'matched','phase':'candidate' if candidate else 'before_rebuild',
            'source_message_shards_checked':source_parts,'target_message_shards_checked':len(message_rels(target)),
            'selected_candidate_tables_checked':target_parts,'auxiliary_tables_checked':matched,
            'message_structure_sha256':_fingerprint(reference),'client_visibility_verified':False}
