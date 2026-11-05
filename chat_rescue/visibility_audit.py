"""Read saved private snapshots to investigate client visibility, never live DBs."""
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from chat_rescue.workflow import connect, ident, table_for, digest, private_json
from chat_rescue.shards import database_rels, message_rels, check_paths
from chat_rescue.operations import fresh
from chat_rescue.runtime import require_private
from chat_rescue.schema_audit import verify_snapshot

TZ=timezone(timedelta(hours=8))


def month(value):
    try:return datetime.fromtimestamp(value,TZ).strftime('%Y-%m')
    except (ValueError,TypeError,OSError,OverflowError):return 'invalid'


def bounds(values):
    values=[value for value in values if type(value) is int]
    return {'min':min(values) if values else None,'max':max(values) if values else None}


def columns(c,table):
    return [row['name'] for row in c.execute('PRAGMA table_info('+ident(table)+')')]


def metadata(c,tables,peer):
    result={}
    for table in ('db_info','config','buff','table_info'):
        if table not in tables:continue
        names=columns(c,table)
        item={'columns':names,'rows':c.execute('SELECT count(*) FROM '+ident(table)).fetchone()[0]}
        # Only numeric timing/version metadata, never arbitrary string/blob values
        # or identity-bearing keys. Unknown metadata stays explicitly unreported.
        item['numeric_time_and_version_entries']=[]
        canonical={name.casefold():name for name in names}
        if {'key','valueint64'}<=set(canonical):
            for row in c.execute('SELECT '+ident(canonical['key'])+','+ident(canonical['valueint64'])+' FROM '+ident(table)):
                key,value=row
                if (isinstance(key,str) and re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]{0,79}',key)
                        and not re.search(r'wxid|user|owner|peer|account|token|secret|key|hash|msg_',key,re.I)
                        and re.search(r'time|version|sequence|sort_seq|rowid|count',key,re.I)
                        and type(value) is int):
                    item['numeric_time_and_version_entries'].append({'key':key,'value':value})
        result[table]=item
    for table in sorted(tables):
        if table.casefold().startswith('wcdb_') and re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]{0,79}',table):
            result[table]={'columns':columns(c,table),
                           'rows':c.execute('SELECT count(*) FROM '+ident(table)).fetchone()[0]}
    if 'DeleteInfo' in tables:
        names=set(columns(c,'DeleteInfo'))
        if {'chat_name_id','delete_table_name'}<=names:
            mapping=c.execute('SELECT rowid FROM Name2Id WHERE user_name=?',(peer,)).fetchone()
            result['selected_chat_deletion_markers']=c.execute(
                'SELECT count(*) FROM DeleteInfo WHERE chat_name_id=? OR delete_table_name=?',
                (mapping[0] if mapping else None,table_for(peer))).fetchone()[0]
    return result


def inspect_snapshot(root,peer,*,verify_export=False):
    root=Path(root);rels=database_rels(root);check_paths(root,rels)
    before=verify_snapshot(root) if verify_export else {rel:digest(root/rel) for rel in rels}
    result={'message_shards':{}}
    table=table_for(peer)
    for rel in message_rels(root):
        with connect(root/rel) as c:
            tables={row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            info={'selected_chat_present':table in tables,'database_metadata':metadata(c,tables,peer),
                  'database_file_bytes':(root/rel).stat().st_size,
                  'sqlite_page_count':c.execute('PRAGMA page_count').fetchone()[0],
                  'sqlite_page_size':c.execute('PRAGMA page_size').fetchone()[0],
                  'sqlite_user_version':c.execute('PRAGMA user_version').fetchone()[0],
                  'sqlite_application_id':c.execute('PRAGMA application_id').fetchone()[0],
                  'sqlite_schema_version':c.execute('PRAGMA schema_version').fetchone()[0]}
            if table in tables:
                names=set(columns(c,table))
                required={'local_id','create_time','sort_seq','local_type','message_content'}
                if not required<=names:raise ValueError('unrecognized message metadata schema')
                marker='WCDB_CT_message_content' if 'WCDB_CT_message_content' in names else 'NULL'
                source_marker='WCDB_CT_source' if 'WCDB_CT_source' in names else 'NULL'
                sql=('SELECT local_id,create_time,sort_seq,local_type,'+marker+' AS compression_marker,'
                     +source_marker+' AS source_compression_marker,typeof(message_content) AS storage_type,'
                     "hex(substr(CAST(message_content AS BLOB),1,4))='28B52FFD' AS zstd_frame FROM "+ident(table))
                records=[dict(row) for row in c.execute(sql)]
                groups=defaultdict(list)
                for row in records:groups[month(row['create_time'])].append(row)
                info['months']={}
                for name,rows in sorted(groups.items()):
                    compressed=Counter((row['compression_marker'],row['source_compression_marker'],
                        row['storage_type'],bool(row['zstd_frame'])) for row in rows)
                    info['months'][name]={'rows':len(rows),'local_id':bounds(r['local_id'] for r in rows),
                        'sort_seq':bounds(r['sort_seq'] for r in rows),
                        'nonpositive_or_missing_sort_seq':sum(type(r['sort_seq']) is not int or r['sort_seq']<=0 for r in rows),
                        'compression_groups':[{'marker':key[0],'source_marker':key[1],
                            'storage_type':key[2],'zstd_frame':key[3],'count':count}
                            for key,count in sorted(compressed.items(),key=lambda item:str(item[0]))]}
                ordered=sorted(records,key=lambda r:((r['sort_seq'] or 0),r['local_id']))
                info['time_backsteps_when_ordered_by_sort_seq']=sum(
                    type(a['create_time']) is int and type(b['create_time']) is int and a['create_time']>b['create_time']
                    for a,b in zip(ordered,ordered[1:]))
                indexes=[]
                for index in c.execute('PRAGMA index_list('+ident(table)+')'):
                    indexes.append({'unique':bool(index['unique']),
                        'columns':[row['name'] for row in c.execute('PRAGMA index_info('+ident(index['name'])+')')]})
                info['indexes']=indexes
                if 'sqlite_sequence' in tables:
                    seq=c.execute('SELECT seq FROM sqlite_sequence WHERE name=?',(table,)).fetchone()
                    info['sqlite_sequence']=seq[0] if seq else None
            result['message_shards'][rel]=info
    with connect(root/'message/message_resource.db') as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result['resource_metadata']={}
        mapping=c.execute('SELECT rowid FROM ChatName2Id WHERE user_name=?',(peer,)).fetchone()
        for table,needed in (('FtsRange',('db_time_stamp','start_local_id','end_local_id','range_type')),
                             ('FtsDeleteInfo',('max_message_id',))):
            if table not in tables:continue
            names=columns(c,table)
            item={'columns':names}
            if {'session_id',*needed}<=set(names) and mapping:
                count=c.execute('SELECT count(*) FROM '+ident(table)+' WHERE session_id=?',(mapping[0],)).fetchone()[0]
                rows=c.execute('SELECT '+','.join(ident(name) for name in needed)+' FROM '+ident(table)
                    +' WHERE session_id=? LIMIT 1000',(mapping[0],)).fetchall()
                # No payload, server ID or account identifier is selected.
                item.update(selected_chat_rows=count,truncated=count>1000,
                    numeric_rows=[{name:row[name] if type(row[name]) is int else None for name in needed} for row in rows])
            result['resource_metadata'][table]=item
    with connect(root/'session/session.db') as c:
        names=set(columns(c,'SessionTable'))
        numeric=[name for name in ('type','is_hidden','status','last_timestamp','sort_timestamp',
            'last_clear_unread_timestamp','last_msg_locald_id') if name in names]
        row=c.execute('SELECT '+','.join(ident(name) for name in numeric)+' FROM SessionTable WHERE username=?',(peer,)).fetchone()
        result['session_numeric_state']={name:row[name] if type(row[name]) is int else None for name in numeric} if row else None
    if any(digest(root/rel)!=value for rel,value in before.items()):
        raise ValueError('snapshot changed during read-only inspection')
    return result


def main():
    print('Client visibility audit / 检查已保存副本，不访问当前微信库',flush=True)
    workspace=Path(os.environ['LOCALAPPDATA'])/'ChatRescue/Desktop'
    settings=workspace/'settings.json';require_private(settings)
    accounts=json.loads(settings.read_text(encoding='utf-8'))['accounts'];a,b=accounts
    choices=[]
    for folder in workspace.glob('history-audit-*'):
        path=folder/'history-audit.json'
        if not path.is_file():continue
        receipt=json.loads(path.read_text(encoding='utf-8'))
        if receipt.get('A_current_export_state')!='verified':continue
        if not isinstance(receipt.get('task_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',receipt['task_id']):
            raise ValueError('invalid saved task identifier')
        task=workspace/'jobs'/receipt['task_id']/'sync'
        manifest=json.loads((task/'sync-plan.json').read_text(encoding='utf-8'))
        if manifest.get('version') not in (4,5) or manifest.get('mode')!='replace-a-from-b':continue
        from chat_rescue.history_audit import snapshot_options
        snapshot_options(manifest)
        if all(manifest['accounts'][slot]['owner']==item['owner'] and
            Path(manifest['accounts'][slot]['root']).resolve()==Path(item['root']).resolve()
            for slot,item in zip(('A','B'),accounts)):
            exported=json.loads((folder/'A-current-plain/export-report.json').read_text(encoding='utf-8'))
            if exported.get('account_directory')!=Path(a['root']).parent.name:continue
            choices.append((path.stat().st_mtime_ns,folder,task))
    if not choices:raise ValueError('no matching completed history audit snapshot')
    _,previous,task=max(choices,key=lambda item:item[0])
    output=fresh(workspace/('visibility-audit-'+uuid.uuid4().hex))
    report={'version':1,'diagnostic_only':True,'live_databases_opened':False,
            'live_databases_modified':False,'basis':'previously_saved_snapshots','state':'running','snapshots':{}}
    def save():
        temporary=output/('report-'+uuid.uuid4().hex+'.tmp');private_json(temporary,report)
        temporary.replace(output/'client-visibility.json')
    save();print('输出目录：'+str(output),flush=True)
    stages=(('B_source',task/'B-plain',a['owner'],True),
            ('A_original',task/'A-plain',b['owner'],True),
            ('A_candidate',task/'A-plan/recovered-plain',b['owner'],False),
            ('A_current',previous/'A-current-plain',b['owner'],True))
    try:
        for index,(label,root,peer,verify) in enumerate(stages,1):
            print(str(index)+'/4 检查 '+label,flush=True)
            report['stage']=label;save()
            report['snapshots'][label]=inspect_snapshot(root,peer,verify_export=verify);save()
        report.update(state='completed',stage='finished');save()
        print('完成：'+str(output/'client-visibility.json'),flush=True)
        print('请只提供 client-visibility.json；没有导出正文，也未修改真实库。',flush=True)
        return 0
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,sqlite3.Error) as error:
        report.update(state='failed',error_class=type(error).__name__);save()
        print('此步骤未完成：'+type(error).__name__+'；请提供 client-visibility.json',flush=True)
        return 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,sqlite3.Error) as error:
        print('启动失败：'+type(error).__name__+'；保留已有任务和检查目录。',flush=True)
        raise SystemExit(1)
