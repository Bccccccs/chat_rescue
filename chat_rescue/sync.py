"""Two-account union, based on immutable pre-application snapshots."""
from .runtime import exclusive_lock, supported_host
import json
import os
import shutil
import uuid
from pathlib import Path
from . import operations as ops
from .voice import MEDIA, CAPABILITY
from . import attachments as att
from .cipher import encrypt_database
from .workflow import RELS, connect, table_for, ident, save_chat, prepare_plain_restore, private_json, digest
from .shards import message_rels, mutable_rels, database_rels, validate_plan_files, check_paths


def conversation_count(plain, peer):
    count = 0
    for rel in message_rels(plain):
        with connect(Path(plain)/rel) as c:
            table = table_for(peer)
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                count += c.execute('SELECT count(*) FROM '+ident(table)).fetchone()[0]
    return count


def make_sync_plan(a, b, output, *, include_voice=False, include_attachments=False):
    supported_host(); ops.ensure_closed()
    if a['owner'] == b['owner'] or Path(a['root']).resolve() == Path(b['root']).resolve():
        raise ValueError('请选择两个不同账号与独立的账号目录')
    accounts = {'A': dict(a), 'B': dict(b)}
    output = Path(output).resolve()
    for account in accounts.values():
        ops.check_owner(account['root'], account['owner'])
        root = Path(account['root']).resolve(strict=True)
        protected_root=root.parent if include_attachments else root
        if output.is_relative_to(protected_root) or protected_root.is_relative_to(output):
            raise ValueError('工作目录必须位于微信数据目录之外')
    output = ops.fresh(output)
    voice_options = {'include_voice': True} if include_voice else {}
    db_options = dict(voice_options)
    if include_attachments:voice_options['include_attachments']=True
    reports, bundles, directions, nicknames = {}, {}, {}, {}
    original_assets = {'A':{}, 'B':{}}
    for slot, account in accounts.items():
        plain = output/(slot+'-plain')
        ops.export_root(account['root'], account['keys'], plain, **voice_options)
        reports[slot] = json.loads((plain/'export-report.json').read_text(encoding='utf-8'))
        with connect(plain/'contact/contact.db') as contact:
            own = contact.execute('SELECT * FROM contact WHERE username=?', (account['owner'],)).fetchone()
            nicknames[slot] = dict(own).get('nick_name') if own else None
        other = accounts['B' if slot == 'A' else 'A']['owner']
        if conversation_count(plain, other):
            bundles[slot] = output/(slot+'-chat')
            save_chat(plain, account['owner'], other, bundles[slot], **voice_options, allow_missing_voice=include_voice, allow_missing_attachments=include_attachments)
            if include_attachments:
                bundle_data = json.loads((bundles[slot]/'conversation.json').read_text())
                original_assets[slot].update(bundle_data['attachment_originals'])
    if not bundles:
        raise ValueError('双方所选会话均无本地记录')
    for slot, account in accounts.items():
        source = 'B' if slot == 'A' else 'A'
        if source not in bundles:
            directions[slot] = {'added': 0, 'skipped': 0, 'state': 'no_source', 'plan': None}
            continue
        directory = ops.fresh(output/(slot+'-plan'))
        try:
            result = prepare_plain_restore(bundles[source], output/(slot+'-plain'), account['owner'], directory/'recovered-plain', **voice_options)
        except ValueError as error:
            if 'collision' not in str(error):
                raise
            target_name = nicknames[slot] or ('账号 ' + slot)
            peer_name = nicknames[source] or ('账号 ' + source)
            if 'another conversation' in str(error):
                raise ValueError(f'{target_name} 的其他会话存在消息编号冲突。删除待恢复会话不能解决，请保留记录并检查错误详情。原因：{error}') from None
            raise ValueError(
                f'{target_name} 的待恢复会话存在消息或资源冲突，本次未写入。'
                f'请保留双方记录和原件，核对 {peer_name} 的源记录与冲突报告。'
                f'不要为恢复删除聊天或修改消息编号。原因：{error}') from None
        # A fully deduplicated direction needs no writes or session modification.
        if include_attachments:
            for rel,h in result['assets']['originals'].items():
                if rel in original_assets[slot] and original_assets[slot][rel] != h:
                    raise ValueError('同一账号的附件在准备期间改变')
                original_assets[slot][rel] = h
        if (not result['added']
                and not ((include_voice or include_attachments) and any(r in (MEDIA, RELS[1]) for r in result['changed_databases']))
                and not (include_attachments and result['assets']['candidates'])):
            directions[slot] = {**result, 'state': 'unchanged', 'plan': None}
            continue
        writes = (*mutable_rels(account['root']), MEDIA) if include_voice else mutable_rels(account['root'])
        if (include_voice or include_attachments) and not result['added']:
            writes = tuple(r for r in result['changed_databases'] if r in (MEDIA, RELS[1]))
        for rel in writes:
            encrypt_database(directory/'recovered-plain'/rel, directory/'candidates'/rel,
                             Path(account['root'])/rel, account['keys'])
        plan = {'version': 3 if include_attachments else 2 if include_voice else 1, 'target_root': str(Path(account['root']).resolve()),
                'target_owner': account['owner'], 'originals': reports[slot]['originals'],
                'candidates': {r:digest(directory/'candidates'/r) for r in writes}, 'report':result}
        if include_voice or include_attachments:
            plan.update(capabilities=([CAPABILITY] if include_voice else [])+([att.CAPABILITY] if include_attachments else []), read_set=list(ops.files_for(include_voice)), write_set=list(writes))
        if include_attachments:
            plan['assets'] = result['assets']
            candidates = directory/'recovered-plain/attachment-candidates'
            if candidates.exists():shutil.move(str(candidates), directory/'attachment-candidates')
        private_json(directory/'plan.json', plan)
        directions[slot] = {**result, 'state': 'ready', 'plan': str(directory)}
    ops.ensure_closed()
    for slot, account in accounts.items():
        if ops.state(account['root'], **db_options) != reports[slot]['originals']:
            raise ValueError('生成预览期间账号库改变，请重新生成')
    manifest = {'version': 3 if include_attachments else 2 if include_voice else 1, 'capabilities': ([CAPABILITY] if include_voice else [])+([att.CAPABILITY] if include_attachments else []), 'accounts': {s:{'owner':c['owner'], 'root':str(Path(c['root']).resolve()),'nickname':nicknames[s] or '昵称未设置'} for s,c in accounts.items()},
                'originals': {s:r['originals'] for s,r in reports.items()}, 'directions':directions}
    if include_attachments:
        manifest['original_assets'] = original_assets
        for slot,account in accounts.items():
            if not att.check_state(Path(account['root']).parent, original_assets[slot]):
                raise ValueError('生成预览期间附件改变，请重新生成')
    private_json(output/'sync-plan.json', manifest)
    return {'plan':str(output), 'directions':directions, 'accounts':manifest['accounts']}


def apply_sync_plan(output):
    supported_host(); ops.ensure_closed()
    output = Path(output).resolve(strict=True)
    with exclusive_lock(output/'.sync-lock'):
        if (output/'sync-started.json').exists():
            raise ValueError('此计划已尝试应用，请查看回执；不要重复执行')
        manifest = json.loads((output/'sync-plan.json').read_text(encoding='utf-8'))
        if manifest.get('version') not in (1,2,3,4) or set(manifest.get('accounts', {})) != {'A','B'}:
            raise ValueError('双向计划结构不受支持')
        voice_options = sync_options(manifest)
        a, b = manifest['accounts']['A'], manifest['accounts']['B']
        if a['owner'] == b['owner'] or Path(a['root']).resolve() == Path(b['root']).resolve():
            raise ValueError('双向计划必须使用两个不同账号')
        for slot, account in manifest['accounts'].items():
            ops.check_owner(account['root'], account['owner'])
            if any((Path(account['root'])/rel).is_symlink() for rel in manifest['originals'][slot]):
                raise ValueError('原数据库或 sidecar 是符号链接，双方均未应用')
            if ops.state(account['root'], **voice_options) != manifest['originals'][slot]:
                raise ValueError('账号 '+slot+' 数据库或 WAL/SHM 已改变；双方均未应用')
            direction = manifest['directions'][slot]
            if direction['plan']:
                directory = (output/(slot+'-plan')).resolve()
                if directory != Path(direction['plan']).resolve():
                    raise ValueError('双向计划路径无效')
                plan = json.loads((directory/'plan.json').read_text(encoding='utf-8'))
                if plan['target_root'] != account['root'] or plan['target_owner'] != account['owner'] or plan['originals'] != manifest['originals'][slot]:
                    raise ValueError('单向计划与双向身份或快照不一致')
                ops.plan_sets(plan)
                expected_version = 1 if manifest.get('mode') == 'replace-a-from-b' else manifest['version']
                if plan['version'] != expected_version or (directory/'apply-started.json').exists():
                    raise ValueError('单向计划不可应用')
                for rel, value in plan['candidates'].items():
                    if digest(ops.protected_path(directory/'candidates', rel)) != value:
                        raise ValueError('候选改变；双方均未应用')
        att.sync_preflight(manifest,output)
        private_json(output/'sync-started.json', {'state':'started'})
        result = {'state':'applied', 'accounts':{}, 'plan':str(output)}
        try:
            for slot, direction in manifest['directions'].items():
                if direction['plan']:
                    result['accounts'][slot] = ops.apply_plan(direction['plan'])
                else:
                    result['accounts'][slot] = {'state':'unchanged', 'added':0}
                result['accounts'][slot]['after'] = ops.state(manifest['accounts'][slot]['root'], **voice_options)
                if manifest['version']==3:
                    root=Path(manifest['accounts'][slot]['root']).parent
                    result['accounts'][slot]['assets_after']={rel:att.file_hash(root,rel) for rel in manifest['original_assets'][slot]}
        except BaseException:
            result['state'] = 'partial' if any(r['state']=='applied' for r in result['accounts'].values()) else 'failed'
            private_json(output/'sync-result.json', result)
            raise RuntimeError('双向应用未全部完成；请查看本机 sync-result.json 与各侧回执，已完成侧的 originals 保留在计划目录') from None
        private_json(output/'sync-result.json', result)
        return result


def undo_sync_plan(output):
    """Undo only an unchanged, fully applied plan; preserve pre-undo files."""
    supported_host(); ops.ensure_closed()
    output=Path(output).resolve(strict=True)
    with exclusive_lock(output/'.sync-lock'):
        if (output/'undo-started.json').exists():
            raise ValueError('撤回已尝试执行，请查看本机回执')
        manifest=json.loads((output/'sync-plan.json').read_text(encoding='utf-8'))
        result=json.loads((output/'sync-result.json').read_text(encoding='utf-8'))
        voice_options = sync_options(manifest)
        if result['state'] != 'applied':
            raise ValueError('仅可自动撤回已全部应用的双向计划；部分完成请保留回执处理')
        if set(result['accounts']) != {'A','B'} or any(set(result['accounts'][s]['after']) != set(manifest['originals'][s]) for s in ('A','B')):
            raise ValueError('撤回回执路径不受支持')
        for slot, account in manifest['accounts'].items():
            ops.check_owner(account['root'],account['owner'])
            allowed = {rel + suffix for rel in ops.inspection_files(account['root'], **voice_options) for suffix in ('', '-wal', '-shm')}
            if (set(manifest['originals'][slot]) != allowed
                    or set(result['accounts'][slot]['after']) != allowed):
                raise ValueError('撤回原件分片及 DB/WAL/SHM 清单不完整或路径无效')
            check_paths(account['root'], allowed)
            check_paths(output/(slot+'-plain')/'_encrypted_snapshot', allowed)
            if any((Path(account['root'])/rel).is_symlink() for rel in result['accounts'][slot]['after']):
                raise ValueError('数据库路径是符号链接，拒绝撤回')
            if ops.state(account['root'], **voice_options) != result['accounts'][slot]['after']:
                raise ValueError('账号 '+slot+' 已产生新记录或库变化，拒绝撤回以保护新消息')
            snapshot=output/(slot+'-plain')/'_encrypted_snapshot'
            for rel,value in manifest['originals'][slot].items():
                p=ops.protected_path(snapshot,rel)
                if (digest(p) if p.exists() else None) != value or p.is_symlink():
                    raise ValueError('撤回原件校验失败')
        undo=ops.fresh(output/('before-undo-'+uuid.uuid4().hex))
        staged=[];changed=[]
        attachment_undo=att.UndoTransaction(output,manifest,result,undo) if manifest['version']==3 else None
        try:
            if attachment_undo:attachment_undo.stage()
            for slot,account in manifest['accounts'].items():
                if result['accounts'][slot]['state'] != 'applied':continue
                for rel,value in result['accounts'][slot]['after'].items():
                    if value is not None:
                        p=undo/slot/rel;p.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copy2(Path(account['root'])/rel,p)
                        if digest(p)!=value:raise ValueError('撤回前备份发生变化')
                for rel,value in manifest['originals'][slot].items():
                    if value is not None:
                        live=Path(account['root'])/rel
                        stage=Path(str(live)+'.chat-rescue-undo-stage')
                        with stage.open('xb') as f, (output/(slot+'-plain')/'_encrypted_snapshot'/rel).open('rb') as src:
                            staged.append(stage)
                            shutil.copyfileobj(src,f);f.flush();os.fsync(f.fileno())
                        stage.chmod(0o600)
            ops.ensure_closed()
            for slot,account in manifest['accounts'].items():
                if ops.state(account['root'], **voice_options)!=result['accounts'][slot]['after']:
                    raise ValueError('撤回前数据库变化')
            if attachment_undo:attachment_undo.check()
            private_json(output/'undo-started.json',{'state':'started'})
            for slot,account in manifest['accounts'].items():
                if result['accounts'][slot]['state']!='applied':continue
                for rel,value in manifest['originals'][slot].items():
                    live=Path(account['root'])/rel;changed.append((slot,rel,live))
                    if value is None:live.unlink(missing_ok=True)
                    else:os.replace(Path(str(live)+'.chat-rescue-undo-stage'),live)
            if attachment_undo:attachment_undo.apply()
            report={'state':'undone','before_undo':str(undo)}
            private_json(output/'undo-result.json',report)
            return report
        except BaseException:
            errors=attachment_undo.rollback() if attachment_undo else []
            for slot,rel,live in reversed(changed):
                try:
                    previous=undo/slot/rel
                    if previous.exists():
                        tmp=Path(str(live)+'.chat-rescue-undo-repair');shutil.copy2(previous,tmp);os.replace(tmp,live)
                    else:live.unlink(missing_ok=True)
                except OSError:errors.append(slot+'/'+rel)
            private_json(undo/'failure.json',{'state':'repair_failed' if errors else ('repaired' if changed else 'preparation_failed'),'failed_paths':errors})
            raise
        finally:
            if attachment_undo:attachment_undo.cleanup()
            for p in staged:p.unlink(missing_ok=True)


def sync_options(manifest):
    version=manifest.get('version');caps=manifest.get('capabilities',[])
    voice=version==2 or (version==3 and CAPABILITY in caps)
    if (version not in (1,2,3,4)
            or set(manifest.get('accounts', {}))!={'A','B'}
            or set(manifest.get('directions', {}))!={'A','B'}
            or set(manifest.get('originals', {}))!={'A','B'}
            or (version==2 and caps!=[CAPABILITY])
            or (version==3 and caps!=([CAPABILITY] if voice else [])+[att.CAPABILITY])):
        raise ValueError('双向计划结构不受支持')
    a,b = manifest['accounts']['A'],manifest['accounts']['B']
    if version == 4 or manifest.get('mode') == 'replace-a-from-b':
        if (version != 4 or manifest.get('mode') != 'replace-a-from-b'
                or caps != ['windows-single-chat-replacement']
                or manifest['directions']['B']['plan'] is not None
                or manifest['directions']['B'].get('added') != 0
                or not manifest['directions']['A']['plan']):
            raise ValueError('单向替换计划只允许写入 A，B 必须保持只读')
    if a['owner']==b['owner'] or Path(a['root']).resolve()==Path(b['root']).resolve():
        raise ValueError('双向计划必须使用两个不同账号')
    for slot,account in manifest['accounts'].items():
        allowed={r+suffix for r in ops.inspection_files(account['root'],voice) for suffix in ('','-wal','-shm')}
        if set(manifest['originals'][slot]) != allowed:
            raise ValueError('双向原件路径不受支持')
    if version==3:
        if set(manifest.get('original_assets',{}))!={'A','B'}:raise ValueError('双向附件原件清单缺失')
        for state in manifest['original_assets'].values():
            for rel,h in state.items():
                att.validate_path(rel)
                if h is not None and (not isinstance(h,str) or len(h)!=64):raise ValueError('附件快照哈希无效')
    return {'include_voice':True} if voice else {}
