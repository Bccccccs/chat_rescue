"""Route a replacement across existing, independently identified target shards."""
from bisect import bisect_right
from collections import defaultdict
import json
from pathlib import Path
import shutil
from .workflow import connect, write_connect, ident, table_for, digest, private_json, prepare_plain_restore
from .shards import message_rels, mutable_rels, database_rels
from .operations import fresh

CAPABILITY='windows-verified-shard-routing'


def routing_inventory(root, owner, peer):
    root=Path(root);rels=message_rels(root)
    if len(rels)==1:
        return {'method':'single-existing-shard','shards':[{'path':rels[0],'timestamp':None,'independent_witnesses':0}]}
    with connect(root/'message/message_resource.db') as c:
        names={r['name'] for r in c.execute('PRAGMA table_info(FtsRange)')}
        if not {'session_id','db_time_stamp','start_local_id','end_local_id','range_type'}<=names:
            raise ValueError('无法核对多分片原生时间标识：缺少兼容 FtsRange；未生成候选')
        ranges=defaultdict(list)
        for row in c.execute('SELECT ch.user_name,f.db_time_stamp,f.start_local_id,f.end_local_id,f.range_type '
                'FROM FtsRange f JOIN ChatName2Id ch ON ch.rowid=f.session_id'):
            name,stamp,start,end,kind=row
            if (name in (owner,peer) or kind not in (1,2) or type(stamp) is not int or stamp<=0
                    or type(start) is not int or type(end) is not int or start<1 or end<start):continue
            ranges[name].append((stamp,start,end))
    # Use only untouched conversations with one native range timestamp, one
    # nonempty physical table, and an exactly matching indexed local-ID tail.
    candidates={name:values for name,values in ranges.items() if len({r[0] for r in values})==1}
    locations=defaultdict(list)
    for rel in rels:
        with connect(root/rel) as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name,values in candidates.items():
                table=table_for(name)
                if table not in tables:continue
                count,tail,first,last=c.execute('SELECT count(*),max(local_id),min(create_time),max(create_time) FROM '+ident(table)).fetchone()
                if count:locations[name].append((rel,tail,first,last))
    witnesses=defaultdict(list)
    for name,locations_for_chat in locations.items():
        if len(locations_for_chat)!=1:continue
        rel,tail,first,last=locations_for_chat[0];values=candidates[name]
        if max(r[2] for r in values)!=tail:continue
        stamp=values[0][0]
        witnesses[(rel,stamp)].append((first,last))
    bindings={};times={}
    for rel in rels:
        choices=[(stamp,values) for (path,stamp),values in witnesses.items() if path==rel]
        if len(choices)!=1 or len(choices[0][1])<2:
            raise ValueError('无法用至少两个未改动会话唯一核对分片时间标识：'+rel+'；未生成候选')
        stamp,values=choices[0]
        if stamp in times:raise ValueError('多个消息分片对应同一时间标识，拒绝猜测')
        bindings[rel]=(stamp,values);times[stamp]=rel
    stamps=sorted(times)
    # The inferred date routing must agree with every independent native witness.
    for rel,(stamp,values) in bindings.items():
        for first,last in values:
            if any(type(value) is not int or value<=0 or
                   times[stamps[max(0,bisect_right(stamps,value)-1)]]!=rel for value in (first,last)):
                raise ValueError('原生会话与分片时间范围不一致，拒绝自动分配：'+rel)
    return {'method':'independent-native-range-witnesses','shards':[
        {'path':times[stamp],'timestamp':stamp,'independent_witnesses':len(bindings[times[stamp]][1])} for stamp in stamps]}


def routed_restore(bundle, base, owner, output, inventory, *, include_voice=False, include_attachments=False, target_asset_root=None):
    bundle,base,output=Path(bundle),Path(base),Path(output)
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') not in ((5,) if include_voice or include_attachments else (1,)) or manifest.get('sha256')!=digest(bundle/'conversation.json'):
        raise ValueError('分片源 bundle 校验失败')
    data=json.loads((bundle/'conversation.json').read_text(encoding='utf-8'))
    if data.get('peer')!=owner or len(data['messages'])!=manifest['message_count']:
        raise ValueError('分片源账号或数量不匹配')
    if include_voice or include_attachments:
        from .media import windows_options
        from .workflow import unpack
        if windows_options(data)!={'include_voice':include_voice,'include_attachments':include_attachments}:
            raise ValueError('分片媒体能力与显式开关不一致')
        # Validate complete manifests and global size bounds before splitting.
        if include_voice:
            from .voice import read_entries
            read_entries(bundle,unpack(data))
        if include_attachments:
            from .attachments import read_entries
            read_entries(bundle,unpack(data))
    definitions=inventory['shards'];groups=defaultdict(list)
    times=[item['timestamp'] for item in definitions]
    for message in data['messages']:
        created=message['fields']['create_time']
        if type(created) is not int or created<=0:raise ValueError('消息时间无法用于原生分片分配')
        index=0 if len(definitions)==1 else max(0,bisect_right(times,created)-1)
        groups[definitions[index]['path']].append(message)
    stage_root=fresh(output.parent/'routing-stages');previous=base
    added=skipped=variants=0;summary=[];result=None
    assets={'originals':{},'candidates':{}};media_counts={};asset_sources={}
    for index,definition in enumerate(definitions):
        rel=definition['path'];messages=groups[rel]
        if not messages:continue
        chunk=fresh(stage_root/('bundle-'+str(index)))
        ids={m['fields']['server_id'] for m in messages}
        subset={**data,'messages':messages,'resources':[r for r in data['resources'] if r['info']['message_svr_id'] in ids]}
        for key in ('voices','attachments'):
            if key in data:subset[key]=[entry for entry in data[key] if entry['server_id'] in ids]
        paths=[entry['audio']['path'] for entry in subset.get('voices',[]) if entry['audio']]
        paths += [item['payload'] for entry in subset.get('attachments',[]) for item in entry['files']]
        for name in paths:
            destination=chunk/name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(bundle/name,destination)
        private_json(chunk/'conversation.json',subset)
        private_json(chunk/'manifest.json',{'version':manifest['version'],'sha256':digest(chunk/'conversation.json'),'message_count':len(messages)})
        next_root=stage_root/('plain-'+str(index))
        result=prepare_plain_restore(chunk,previous,owner,next_root,destination_shard=rel,
            include_voice=include_voice,include_attachments=include_attachments,target_asset_root=target_asset_root)
        for key,value in result.items():
            if key.startswith(('voice_','image_','video_','file_','attachments_','attachment_resource_')) and type(value) is int:
                media_counts[key]=media_counts.get(key,0)+value
        if include_attachments:
            for key in assets:
                for name,value in result['assets'][key].items():
                    if name in assets[key] and assets[key][name]!=value:raise ValueError('跨分片附件路径或内容冲突')
                    assets[key][name]=value
                    if key=='candidates':asset_sources[name]=next_root/'attachment-candidates'/name
        added+=result['added'];skipped+=result['skipped'];variants+=result['timestamp_variants_preserved']
        summary.append({**definition,'imported':result['added'],
            'first_timestamp':min(m['fields']['create_time'] for m in messages),
            'last_timestamp':max(m['fields']['create_time'] for m in messages)})
        previous=next_root
    if result is None:raise ValueError('没有可导入的普通消息')
    output=fresh(output)
    from .voice import MEDIA
    for rel in (*database_rels(previous), *((MEDIA,) if include_voice else ())):
        dest=output/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(previous/rel,dest)
    shutil.copy2(previous/'direction-profile.json',output/'direction-profile.json')
    for name,path in asset_sources.items():
        dest=output/'attachment-candidates'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    # Old local-ID coverage cannot claim that the re-created rows were indexed.
    # Invalidate only this chat's stale coverage; never fabricate completed FTS.
    invalidated=0
    with write_connect(output/'message/message_resource.db') as c:
        mapping=c.execute('SELECT rowid FROM ChatName2Id WHERE user_name=?',(data['owner'],)).fetchone()
        present=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='FtsRange'").fetchone()
        if mapping and present:
            if any(row[0] not in (1,2) for row in c.execute('SELECT range_type FROM FtsRange WHERE session_id=?',(mapping[0],))):
                raise ValueError('所选会话存在未知搜索范围类型，未生成可应用候选')
            invalidated=c.execute('DELETE FROM FtsRange WHERE session_id=?',(mapping[0],)).rowcount
    result.update(added=added,skipped=skipped,timestamp_variants_preserved=variants,
        source_message_shards=data.get('source_shards',[]),target_shard_imports=summary,
        shard_routing=inventory,invalidated_fts_ranges=invalidated,client_search_index_verified=False,
        changed_databases=[rel for rel in (*mutable_rels(base), *((MEDIA,) if include_voice else ())) if digest(base/rel)!=digest(output/rel)],
        **media_counts)
    if include_attachments:result['assets']=assets
    # Verify IDs, timestamps, shard destination and role transformation before encryption.
    expected={m['fields']['server_id']:(m['fields']['create_time'],rel)
              for rel,messages in groups.items() for m in messages}
    actual={}
    for rel in message_rels(output):
        with connect(output/rel) as c:
            table=table_for(data['owner'])
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():continue
            for sid,created in c.execute('SELECT server_id,create_time FROM '+ident(table)):
                if sid in actual:raise ValueError('候选跨分片存在重复消息编号')
                actual[sid]=(created,rel)
    if actual!=expected:raise ValueError('分片候选的消息编号、时间或分布不匹配')
    return result
