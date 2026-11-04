"""Offline snapshots, plan generation and coordinated reversible replacement."""
import json
import os
import platform
from pathlib import Path
import shutil
from .workflow import _single_shard, RELS, digest, private_json, prepare_plain_restore, LEGACY_PROFILE, WINDOWS_STRUCTURED_PROFILE
from .cipher import export_database, encrypt_database
from .voice import MEDIA, CAPABILITY, single_media
from .runtime import supported_host, ensure_closed, exclusive_lock, make_private
from .shards import message_shards, database_rels, mutable_rels, validate_plan_files, check_paths, receipt_covers

ALL = (*RELS, 'contact/contact.db')


def mac_only():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('此取钥流程仅支持 macOS Apple Silicon；Windows 请使用已有授权密钥及 verify-keys')


def check_owner(root, owner):
    account = Path(root).resolve().parent.name
    if not (account == owner or account.startswith(owner + '_')):
        raise ValueError('账号目录与 --owner 不匹配；不要从昵称猜测内部 wxid')


def files_for(include_voice=False):
    if include_voice:
        return (*ALL, MEDIA)
    return ALL


def inspection_files(root, include_voice=False):
    """Read all existing shards on both platforms; media adds its own database."""
    return (*database_rels(root), *((MEDIA,) if include_voice else ()))



def plan_sets(plan):
    """Versioned fixed database allowlist, plus bounded account media paths."""
    from . import attachments as att
    version = plan.get('version')
    caps = plan.get('capabilities', [])
    voice = version == 2 or (version == 3 and CAPABILITY in caps)
    if (version not in (1,2,3)
            or (version == 2 and caps != [CAPABILITY])
            or (version == 3 and caps != ([CAPABILITY] if voice else [])+[att.CAPABILITY])):
        raise ValueError('计划格式不受支持')
    if version == 3:
        att.validate_plan(plan)
        if platform.system() == 'Windows':raise ValueError('Windows附件恢复尚未适配')
    root = plan.get('target_root')
    if not root:
        raise ValueError('计划缺少目标目录')
    if voice or version == 3 or platform.system() == 'Darwin':
        _single_shard(root)
    read_set = inspection_files(root, voice)
    write_set = tuple(plan.get('write_set', ())) if version in (2,3) else mutable_rels(root)
    allowed_writes = (*mutable_rels(root), MEDIA) if voice else mutable_rels(root)
    if ((not write_set and not (version==3 and plan['assets']['candidates']))
            or len(set(write_set)) != len(write_set)
            or not set(write_set) <= set(allowed_writes)
            or (version in (2,3) and plan.get('read_set') != list(read_set))
            or set(plan['candidates']) != set(write_set)
            or set(plan['originals']) != {r+suffix for r in read_set for suffix in ('','-wal','-shm')}):
        raise ValueError('计划原件路径或写入集合不受支持')
    return read_set, write_set


def state(root, include_voice=False):
    if include_voice:
        single_media(root)
    return {rel + suffix: digest(p) if p.exists() else None
            for rel in inspection_files(root, include_voice) for suffix in ('', '-wal', '-shm')
            for p in [protected_path(root, rel + suffix)]}


def protected_path(root, relative):
    path = Path(root)
    if path.is_symlink():
        raise ValueError('数据库或候选根目录使用符号链接，拒绝操作')
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('数据库、候选或其父目录使用符号链接，拒绝操作')
    return path


def fresh(path):
    os.umask(0o077)
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=False, mode=0o700)
    make_private(p)
    return p


def _copy_snapshot(root, output, include_voice=False):
    before = state(root, include_voice)
    for rel in inspection_files(root, include_voice):
        if before[rel] is None:
            raise ValueError('缺少必要数据库：' + rel)
    for rel, value in before.items():
        if value is not None:
            p = output / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(root) / rel, p)
            p.chmod(0o600)
            if digest(p) != value:
                raise ValueError('数据库复制期间发生变化')
    ensure_closed()
    if state(root, include_voice) != before:
        raise ValueError('复制期间微信库发生变化，请退出后使用新目录重试')
    return before


def export_root(root, keys, output, *, include_voice=False, include_attachments=False):
    supported_host(); ensure_closed()
    root = Path(root).resolve(strict=True)
    output = Path(output).resolve()
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('导出目录必须位于微信数据目录之外')
    profile = capture_direction_profile(root, keys)
    if (include_voice or include_attachments) and (profile != LEGACY_PROFILE or platform.system() == 'Windows'):
        raise ValueError('媒体实验模式仅适配 Mac；Windows 尚未适配语音/附件')
    files = inspection_files(root, include_voice)
    output = fresh(output)
    snapshot = output / '_encrypted_snapshot'
    before = _copy_snapshot(root, snapshot, include_voice)
    if files != inspection_files(snapshot, include_voice):
        raise ValueError('快照消息分片清单不匹配，请重新导出')
    # Keys can be bound to original absolute paths. Resolve them against originals
    # then bind to snapshot relative paths in a temporary private manifest.
    from .vendor.sqlcipher_probe import load_key
    copied_keys = output / '_snapshot_keys.json'
    private_json(copied_keys, {'keys': {rel: {'enc_key': load_key(keys, root / rel)} for rel in files}})
    try:
        for rel in files:
            export_database(snapshot / rel, output / rel, copied_keys)
    finally:
        copied_keys.unlink(missing_ok=True)
    if state(root, include_voice) != before:
        raise ValueError('导出期间原库改变，当前导出不可用于恢复')
    report = {'version': 3 if include_attachments else 2 if include_voice else 1, 'capabilities': [CAPABILITY] if include_voice else [], 'account_directory': root.parent.name, 'originals': before, 'direction_profile': profile,
              'message_shards': list(message_shards(snapshot)),
              'exported': {rel: digest(output / rel) for rel in files}}
    if include_attachments:
        report['attachment_root'] = str(root.parent)
        from .attachments import CAPABILITY as ATTACH_CAP
        report['capabilities'].append(ATTACH_CAP)
    private_json(output / 'export-report.json', report)
    return {'report': str(output / 'export-report.json'), 'database_count': len(files)}


def capture_direction_profile(root, keys):
    """Use a matching local capture receipt, never infer database layout from host OS."""
    receipt = Path(keys).with_suffix('.capture.json')
    if not receipt.exists():
        return LEGACY_PROFILE
    value = json.loads(receipt.read_text(encoding='utf-8'))
    if (value.get('state') != 'verified' or not receipt_covers(root, value)
            or Path(value.get('database_root', '')).resolve() != Path(root).resolve()):
        raise ValueError('取钥回执与导出账号目录不匹配')
    check_owner(root, value.get('owner') or 'invalid-owner')
    if value.get('file_version') != '4.2.0.12':
        raise ValueError('此 Windows 完整版本尚未验证消息方向，不能生成恢复候选')
    return WINDOWS_STRUCTURED_PROFILE


def make_plan(source, root, keys, owner, output, *, include_voice=False, include_attachments=False):
    supported_host(); ensure_closed()
    root = Path(root).resolve(strict=True)
    check_owner(root, owner)
    if include_attachments and (Path(output).resolve().is_relative_to(root.parent) or root.parent.is_relative_to(Path(output).resolve())):
        raise ValueError('附件计划必须位于微信账号目录之外')
    output = fresh(output)
    # Candidate/data paths must never be within live db_storage.
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('计划目录必须位于微信数据目录之外')
    exported = output / 'target-plain'
    export_root(root, keys, exported, include_voice=include_voice, include_attachments=include_attachments)
    report = json.loads((exported / 'export-report.json').read_text(encoding='utf-8'))
    result = prepare_plain_restore(source, exported, owner, output / 'recovered-plain', include_voice=include_voice, include_attachments=include_attachments)
    voice = (exported / MEDIA).exists() and include_voice
    writes = (*mutable_rels(root), MEDIA) if voice else mutable_rels(root)
    if (voice or include_attachments) and not result['added']:
        writes = tuple(r for r in result['changed_databases'] if r in (MEDIA, RELS[1]))
        if not writes and not (include_attachments and result['assets']['candidates']):
            raise ValueError('没有需要应用的语音或消息变化')
    for rel in writes:
        encrypt_database(output / 'recovered-plain' / rel, output / 'candidates' / rel, root / rel, keys)
    ensure_closed()
    if state(root, include_voice) != report['originals']:
        raise ValueError('生成计划期间目标库改变')
    plan = {'version': 3 if include_attachments else 2 if voice else 1, 'target_root': str(root), 'target_owner': owner,
            'originals': report['originals'], 'candidates': {r: digest(output / 'candidates' / r) for r in writes},
            'report': result}
    if voice or include_attachments:
        from .attachments import CAPABILITY as ATTACH_CAP
        plan.update(capabilities=([CAPABILITY] if voice else [])+([ATTACH_CAP] if include_attachments else []), read_set=list(files_for(voice)), write_set=list(writes))
    if include_attachments:
        plan['assets'] = result['assets']
        candidates = output/'recovered-plain/attachment-candidates'
        if candidates.exists():
            shutil.move(str(candidates), output/'attachment-candidates')
    private_json(output / 'plan.json', plan)
    private_json(output / 'preview.json', {'target_owner': owner, **result})
    return {'preview': str(output / 'preview.json'), 'plan': str(output), **result}


def apply_plan(directory):
    supported_host(); ensure_closed()
    directory = Path(directory).resolve(strict=True)
    with exclusive_lock(directory / '.lock'):
        plan = json.loads((directory / 'plan.json').read_text(encoding='utf-8'))
        read_set, writes = plan_sets(plan)
        include_voice = MEDIA in read_set
        if (directory / 'apply-started.json').exists():
            raise ValueError('计划已尝试应用；请检查回执并重新生成计划')
        root = Path(plan['target_root']).resolve(strict=True)
        check_owner(root, plan['target_owner'])
        if plan['version'] == 1:
            validate_plan_files(root, plan)
        check_paths(directory / 'candidates', writes)
        if directory.is_relative_to(root) or root.is_relative_to(directory):
            raise ValueError('计划与原库目录必须分离')
        if include_voice:
            single_media(root)
        if include_voice or plan['version'] == 3 or platform.system() == 'Darwin':
            _single_shard(root)
        allowed = {r + s for r in read_set for s in ('', '-wal', '-shm')}
        if set(plan['originals']) != allowed:
            raise ValueError('计划原件路径不受支持')
        for rel, h in plan['originals'].items():
            p = protected_path(root, rel)
            if p.is_symlink() or (digest(p) if p.exists() else None) != h:
                raise ValueError('原库或 WAL/SHM 已改变，拒绝应用')
        for rel, h in plan['candidates'].items():
            p = protected_path(directory / 'candidates', rel)
            if p.is_symlink() or digest(p) != h:
                raise ValueError('候选已改变，拒绝应用')
        backup = directory / 'originals'
        backup.mkdir(mode=0o700)
        staged, replaced = [], []
        attachment_transaction = None
        if plan['version'] == 3:
            from .attachments import Transaction
            attachment_transaction = Transaction(plan,directory)
        # Copy every original before the first replacement. WAL/SHM is essential.
        for rel, h in plan['originals'].items():
            if h is not None:
                q = backup / rel
                q.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / rel, q)
                q.chmod(0o600)
                with q.open('r+b') as f:
                    os.fsync(f.fileno())
                sync_directory(q.parent)
                if digest(q) != h:
                    raise ValueError('原件备份哈希不匹配')
        try:
            if attachment_transaction:
                attachment_transaction.stage()
            for rel in writes:
                p = Path(str(root / rel) + '.chat-rescue-stage')
                if p.exists():
                    raise ValueError('发现未清理的暂存文件，请检查之前的操作')
                with p.open('xb') as f, (directory / 'candidates' / rel).open('rb') as src:
                    staged.append(p)
                    shutil.copyfileobj(src, f)
                    f.flush(); os.fsync(f.fileno())
                p.chmod(0o600)
            ensure_closed()
            if state(root, include_voice) != plan['originals']:
                raise ValueError('应用前目标库或分片清单改变')
            for rel, h in plan['originals'].items():
                p = protected_path(root, rel)
                if (digest(p) if p.exists() else None) != h:
                    raise ValueError('应用前目标库改变')
            if attachment_transaction:
                attachment_transaction.check()
            private_json(directory / 'apply-started.json', {'state': 'started', 'backup': str(backup)})
            if include_voice:
                single_media(root)
            if include_voice or plan['version'] == 3 or platform.system() == 'Darwin':
                _single_shard(root)
            for rel, p in zip(writes, staged):
                # Mark before touching sidecars so rollback also repairs this DB.
                journal(directory, {'state': 'replace_intent', 'database': rel})
                replaced.append(rel)
                for suffix in ('-wal', '-shm'):
                    Path(str(root / rel) + suffix).unlink(missing_ok=True)
                os.replace(p, root / rel)
                sync_directory((root / rel).parent)
                journal(directory, {'state': 'replace_done', 'database': rel})
            if attachment_transaction:
                attachment_transaction.apply()
            private_json(directory / 'applied.json', {'state': 'applied', 'backup': str(backup), **plan['report']})
        except BaseException:
            errors = attachment_transaction.rollback() if attachment_transaction else []
            for rel in reversed(replaced):
                for suffix in ('', '-wal', '-shm'):
                    name = rel + suffix
                    try:
                        original, live = backup / name, root / name
                        if original.exists():
                            tmp = Path(str(live) + '.chat-rescue-rollback')
                            shutil.copy2(original, tmp); os.replace(tmp, live)
                            sync_directory(live.parent)
                        else:
                            live.unlink(missing_ok=True)
                    except OSError:
                        errors.append(name)
            if replaced or (attachment_transaction and attachment_transaction.replaced):
                private_json(directory / 'rollback.json', {'state': 'rollback_failed' if errors else 'rolled_back', 'failed_paths': errors, 'backup': str(backup)})
            raise
        finally:
            if attachment_transaction:
                attachment_transaction.cleanup()
            for p in staged:
                p.unlink(missing_ok=True)
        return {'state': 'applied', 'receipt': str(directory / 'applied.json'), 'backup': str(backup), **plan['report']}


def sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def journal(directory, event):
    with (directory / 'apply-journal.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(event, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())
    sync_directory(directory)
