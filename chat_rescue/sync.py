"""Two-account union, based on immutable pre-application snapshots."""
import fcntl
import json
import os
import shutil
import uuid
from pathlib import Path
from . import operations as ops
from .cipher import encrypt_database
from .workflow import RELS, connect, table_for, ident, save_chat, prepare_plain_restore, private_json, digest


def conversation_count(plain, peer):
    with connect(Path(plain)/RELS[0]) as c:
        table = table_for(peer)
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return 0
        return c.execute('SELECT count(*) FROM '+ident(table)).fetchone()[0]


def make_sync_plan(a, b, output):
    ops.mac_only(); ops.ensure_closed()
    if a['owner'] == b['owner'] or Path(a['root']).resolve() == Path(b['root']).resolve():
        raise ValueError('请选择两个不同账号与独立的账号目录')
    accounts = {'A': dict(a), 'B': dict(b)}
    output = Path(output).resolve()
    for account in accounts.values():
        ops.check_owner(account['root'], account['owner'])
        root = Path(account['root']).resolve(strict=True)
        if output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError('工作目录必须位于微信数据目录之外')
    output = ops.fresh(output)
    reports, bundles, directions, nicknames = {}, {}, {}, {}
    for slot, account in accounts.items():
        plain = output/(slot+'-plain')
        ops.export_root(account['root'], account['keys'], plain)
        reports[slot] = json.loads((plain/'export-report.json').read_text())
        with connect(plain/'contact/contact.db') as contact:
            own = contact.execute('SELECT * FROM contact WHERE username=?', (account['owner'],)).fetchone()
            nicknames[slot] = dict(own).get('nick_name') if own else None
        other = accounts['B' if slot == 'A' else 'A']['owner']
        if conversation_count(plain, other):
            bundles[slot] = output/(slot+'-chat')
            save_chat(plain, account['owner'], other, bundles[slot])
    if not bundles:
        raise ValueError('双方所选会话均无本地记录')
    for slot, account in accounts.items():
        source = 'B' if slot == 'A' else 'A'
        if source not in bundles:
            directions[slot] = {'added': 0, 'skipped': 0, 'state': 'no_source', 'plan': None}
            continue
        directory = ops.fresh(output/(slot+'-plan'))
        try:
            result = prepare_plain_restore(bundles[source], output/(slot+'-plain'), account['owner'], directory/'recovered-plain')
        except ValueError as error:
            if 'collision' not in str(error):
                raise
            target_name = nicknames[slot] or ('账号 ' + slot)
            peer_name = nicknames[source] or ('账号 ' + source)
            if 'another conversation' in str(error):
                raise ValueError(f'{target_name} 的其他会话存在消息编号冲突。删除待恢复会话不能解决，请保留记录并检查错误详情。原因：{error}') from None
            raise ValueError(
                f'{target_name} 的待恢复会话存在消息或资源冲突，本次未写入。'
                f'可先备份并确认 {peer_name} 保留完整记录，再在 {target_name} 的电脑微信中'
                f'手动删除与 {peer_name} 的这段聊天，正常退出微信后重新读取。'
                f'删除会丢失该会话仅在本地存在的记录；不确定时请保留记录。原因：{error}') from None
        # A fully deduplicated direction needs no writes or session modification.
        if not result['added']:
            directions[slot] = {**result, 'state': 'unchanged', 'plan': None}
            continue
        for rel in RELS:
            encrypt_database(directory/'recovered-plain'/rel, directory/'candidates'/rel,
                             Path(account['root'])/rel, account['keys'])
        plan = {'version': 1, 'target_root': str(Path(account['root']).resolve()),
                'target_owner': account['owner'], 'originals': reports[slot]['originals'],
                'candidates': {r:digest(directory/'candidates'/r) for r in RELS}, 'report':result}
        private_json(directory/'plan.json', plan)
        directions[slot] = {**result, 'state': 'ready', 'plan': str(directory)}
    ops.ensure_closed()
    for slot, account in accounts.items():
        if ops.state(account['root']) != reports[slot]['originals']:
            raise ValueError('生成预览期间账号库改变，请重新生成')
    manifest = {'version': 1, 'accounts': {s:{'owner':c['owner'], 'root':str(Path(c['root']).resolve()),'nickname':nicknames[s] or '昵称未设置'} for s,c in accounts.items()},
                'originals': {s:r['originals'] for s,r in reports.items()}, 'directions':directions}
    private_json(output/'sync-plan.json', manifest)
    return {'plan':str(output), 'directions':directions, 'accounts':manifest['accounts']}


def apply_sync_plan(output):
    ops.mac_only(); ops.ensure_closed()
    output = Path(output).resolve(strict=True)
    with (output/'.sync-lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (output/'sync-started.json').exists():
            raise ValueError('此计划已尝试应用，请查看回执；不要重复执行')
        manifest = json.loads((output/'sync-plan.json').read_text())
        if manifest.get('version') != 1 or set(manifest['accounts']) != {'A','B'}:
            raise ValueError('双向计划结构不受支持')
        a, b = manifest['accounts']['A'], manifest['accounts']['B']
        if a['owner'] == b['owner'] or Path(a['root']).resolve() == Path(b['root']).resolve():
            raise ValueError('双向计划必须使用两个不同账号')
        for slot, account in manifest['accounts'].items():
            ops.check_owner(account['root'], account['owner'])
            if any((Path(account['root'])/rel).is_symlink() for rel in manifest['originals'][slot]):
                raise ValueError('原数据库或 sidecar 是符号链接，双方均未应用')
            if ops.state(account['root']) != manifest['originals'][slot]:
                raise ValueError('账号 '+slot+' 数据库或 WAL/SHM 已改变；双方均未应用')
            direction = manifest['directions'][slot]
            if direction['plan']:
                directory = (output/(slot+'-plan')).resolve()
                if directory != Path(direction['plan']).resolve():
                    raise ValueError('双向计划路径无效')
                plan = json.loads((directory/'plan.json').read_text())
                if plan['target_root'] != account['root'] or plan['target_owner'] != account['owner'] or plan['originals'] != manifest['originals'][slot]:
                    raise ValueError('单向计划与双向身份或快照不一致')
                if set(plan['candidates']) != set(RELS) or (directory/'apply-started.json').exists():
                    raise ValueError('单向计划不可应用')
                for rel, value in plan['candidates'].items():
                    if (directory/'candidates'/rel).is_symlink() or digest(directory/'candidates'/rel) != value:
                        raise ValueError('候选改变；双方均未应用')
        private_json(output/'sync-started.json', {'state':'started'})
        result = {'state':'applied', 'accounts':{}, 'plan':str(output)}
        try:
            for slot, direction in manifest['directions'].items():
                if direction['plan']:
                    result['accounts'][slot] = ops.apply_plan(direction['plan'])
                else:
                    result['accounts'][slot] = {'state':'unchanged', 'added':0}
                result['accounts'][slot]['after'] = ops.state(manifest['accounts'][slot]['root'])
        except BaseException:
            result['state'] = 'partial' if any(r['state']=='applied' for r in result['accounts'].values()) else 'failed'
            private_json(output/'sync-result.json', result)
            raise RuntimeError('双向应用未全部完成；请查看本机 sync-result.json 与各侧回执，已完成侧的 originals 保留在计划目录') from None
        private_json(output/'sync-result.json', result)
        return result


def undo_sync_plan(output):
    """Undo only an unchanged, fully applied plan; preserve pre-undo files."""
    ops.mac_only(); ops.ensure_closed()
    output=Path(output).resolve(strict=True)
    with (output/'.sync-lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (output/'undo-started.json').exists():
            raise ValueError('撤回已尝试执行，请查看本机回执')
        manifest=json.loads((output/'sync-plan.json').read_text())
        result=json.loads((output/'sync-result.json').read_text())
        if result['state'] != 'applied':
            raise ValueError('仅可自动撤回已全部应用的双向计划；部分完成请保留回执处理')
        for slot, account in manifest['accounts'].items():
            ops.check_owner(account['root'],account['owner'])
            if any((Path(account['root'])/rel).is_symlink() for rel in result['accounts'][slot]['after']):
                raise ValueError('数据库路径是符号链接，拒绝撤回')
            if ops.state(account['root']) != result['accounts'][slot]['after']:
                raise ValueError('账号 '+slot+' 已产生新记录或库变化，拒绝撤回以保护新消息')
            snapshot=output/(slot+'-plain')/'_encrypted_snapshot'
            for rel,value in manifest['originals'][slot].items():
                p=snapshot/rel
                if (digest(p) if p.exists() else None) != value or p.is_symlink():
                    raise ValueError('撤回原件校验失败')
        undo=ops.fresh(output/('before-undo-'+uuid.uuid4().hex))
        staged=[];changed=[]
        try:
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
                if ops.state(account['root'])!=result['accounts'][slot]['after']:
                    raise ValueError('撤回前数据库变化')
            private_json(output/'undo-started.json',{'state':'started'})
            for slot,account in manifest['accounts'].items():
                if result['accounts'][slot]['state']!='applied':continue
                for rel,value in manifest['originals'][slot].items():
                    live=Path(account['root'])/rel;changed.append((slot,rel,live))
                    if value is None:live.unlink(missing_ok=True)
                    else:os.replace(Path(str(live)+'.chat-rescue-undo-stage'),live)
            report={'state':'undone','before_undo':str(undo)}
            private_json(output/'undo-result.json',report)
            return report
        except BaseException:
            errors=[]
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
            for p in staged:p.unlink(missing_ok=True)
