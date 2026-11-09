"""Explain schema guard failures using already exported private snapshots only."""
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from chat_rescue.workflow import connect, digest, private_json, table_for
from chat_rescue.schema_checks import table_shape, _aux_match
from chat_rescue.shards import database_rels, message_rels, check_paths
from chat_rescue.operations import fresh
from chat_rescue.runtime import require_private
from chat_rescue.routing import routing_inventory


def safe_sql(value):
    """DDL only; strip literals and recognizable account/conversation tokens."""
    if value is None:return None
    value=re.sub(r"'(?:''|[^'])*'", "'<literal>'", str(value))
    value=re.sub(r'(?i)Msg_[0-9a-f]{32}', 'Msg_SELECTED', value)
    value=re.sub(r'(?i)wxid_[a-z0-9_]+', '<account>', value)
    return value


def safe_shape(shape):
    if shape is None:return None
    return {'columns':[[safe_sql(value) if isinstance(value,str) else value for value in row] for row in shape['columns']],
            'definition':safe_sql(shape['definition']),
            'indexes':[{'unique':item['unique'],'origin':item['origin'],'partial':item['partial'],
                'fields':[[safe_sql(value) if isinstance(value,str) else value for value in row] for row in item['fields']],
                'expression':safe_sql(item['expression'])} for item in shape['indexes']],
            'foreign_keys':[[safe_sql(value) if isinstance(value,str) else value for value in row] for row in shape['foreign_keys']],
            'trigger_count':len(shape['triggers'])}


def named_shapes(c,*,message=False):
    result={}
    for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        name=row[0]
        if message and name.startswith('Msg_'):continue
        # Dynamic user/conversation table names are never emitted in a report.
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,99}',name) or re.search(r'wxid_|[0-9a-f]{32}',name,re.I):
            raise ValueError('unsafe dynamic metadata table name')
        result[name]=table_shape(c,name)
    return result


def differences(left,right):
    result=[]
    for name in sorted(set(left)|set(right)):
        a,b=left.get(name),right.get(name)
        if a==b:continue
        fields=[key for key in ('columns','definition','indexes','foreign_keys','triggers')
                if a is None or b is None or a[key]!=b[key]]
        result.append({'table':name,'different_components':fields,'left':safe_shape(a),'right':safe_shape(b)})
    return result


def verify_snapshot(root):
    root=Path(root);rels=database_rels(root)
    receipt=json.loads((root/'export-report.json').read_text(encoding='utf-8'))
    recorded=receipt.get('exported')
    from .voice import single_media
    from .voice import is_media_rel
    media = single_media(root) if any(is_media_rel(name) for name in (recorded or {})) else ()
    if not isinstance(recorded,dict) or set(recorded)!=set((*rels,*media)):
        raise ValueError('unsupported saved export inventory')
    check_paths(root,recorded)
    hashes={rel:digest(root/rel) for rel in recorded}
    if hashes!=receipt.get('exported'):raise ValueError('saved export hash mismatch')
    return hashes


def inspect_pair(a_root,b_root,a_owner,b_owner):
    a_root,b_root=Path(a_root),Path(b_root)
    originals={root:verify_snapshot(root) for root in (a_root,b_root)}
    result={'version':1,'diagnostic_only':True,'live_databases_opened':False,
            'live_databases_modified':False,'state':'completed','message_shards':[],'companion_databases':[]}
    baseline_aux=None;baseline_message=None;baseline_rel=None
    for slot,root,peer in (('B',b_root,a_owner),('A',a_root,b_owner)):
        for rel in message_rels(root):
            with connect(root/rel) as c:
                aux=named_shapes(c,message=True);selected=table_shape(c,table_for(peer))
            if baseline_aux is None:
                baseline_aux=aux;baseline_message=selected;baseline_rel=rel
            item={'account_role':slot,'database':rel,'reference_database':'B:'+baseline_rel,
                'auxiliary_table_names':sorted(aux),'auxiliary_guard_matches':_aux_match(baseline_aux,aux),
                'auxiliary_differences':differences(baseline_aux,aux),
                'selected_chat_table_present':selected is not None,
                'selected_chat_structure_matches':selected==baseline_message if selected is not None else None}
            if selected is not None and selected!=baseline_message:
                item['selected_chat_structure_differences']=differences({'selected_chat':baseline_message},{'selected_chat':selected})
            result['message_shards'].append(item)
    for rel in ('message/message_resource.db','session/session.db','contact/contact.db'):
        values=[]
        for root in (b_root,a_root):
            with connect(root/rel) as c:values.append(named_shapes(c))
        result['companion_databases'].append({'database':rel,'left_role':'B','right_role':'A',
            'structure_matches':values[0]==values[1],'differences':differences(*values)})
    try:result['target_routing_check']={'state':'verified','report':routing_inventory(a_root,a_owner,b_owner)}
    except (ValueError,sqlite3.Error) as error:
        result['target_routing_check']={'state':'not_verified','error_class':type(error).__name__}
        # Only the validator's fixed messages and canonical relative shard paths.
        if isinstance(error,ValueError):result['target_routing_check']['reason']=str(error)
    for root,hashes in originals.items():
        if any(digest(root/rel)!=value for rel,value in hashes.items()):
            raise ValueError('saved export changed during schema inspection')
    return result


def main():
    print('Schema difference audit / 只检查已保存副本，不访问当前微信库',flush=True)
    workspace=Path(os.environ['LOCALAPPDATA'])/'ChatRescue/Desktop'
    require_private(workspace/'settings.json')
    a,b=json.loads((workspace/'settings.json').read_text(encoding='utf-8'))['accounts']
    choices=[]
    for task in (workspace/'jobs').glob('*/sync'):
        receipts=[task/(slot+'-plain/export-report.json') for slot in ('A','B')]
        if not all(path.is_file() for path in receipts):continue
        if all(json.loads(path.read_text(encoding='utf-8')).get('account_directory')==Path(account['root']).parent.name
               for path,account in zip(receipts,(a,b))):
            choices.append((max(path.stat().st_mtime_ns for path in receipts),task))
    if not choices:raise ValueError('no matching saved A/B exports')
    task=max(choices,key=lambda item:item[0])[1]
    output=fresh(workspace/('schema-audit-'+uuid.uuid4().hex))
    print('检查已有 A/B 分片与辅助库；输出目录：'+str(output),flush=True)
    report=inspect_pair(task/'A-plain',task/'B-plain',a['owner'],b['owner'])
    private_json(output/'schema-diff.json',report)
    print('完成：'+str(output/'schema-diff.json'),flush=True)
    print('请只提供 schema-diff.json；报告不含聊天正文、密钥或真实消息编号。',flush=True)


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,sqlite3.Error) as error:
        print('检查未完成：'+type(error).__name__+'；保留当前窗口及已有任务。',flush=True)
        raise SystemExit(1)
