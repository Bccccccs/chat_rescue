"""Offline snapshots, plan generation and coordinated reversible replacement."""
import fcntl
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
from .workflow import RELS, digest, private_json, prepare_plain_restore, _single_shard
from .cipher import export_database, encrypt_database

ALL = (*RELS, 'contact/contact.db')


def mac_only():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('首版仅支持 macOS Apple Silicon')


def ensure_closed():
    rows = subprocess.run(['/bin/ps', '-axo', 'comm='], check=True, capture_output=True, text=True).stdout.splitlines()
    if any(Path(r.strip()).name in ('WeChat', 'Weixin', '微信') for r in rows):
        raise RuntimeError('请用 ⌘Q 正常退出原版及副本微信后重试')


def check_owner(root, owner):
    account = Path(root).resolve().parent.name
    if not (account == owner or account.startswith(owner + '_')):
        raise ValueError('账号目录与 --owner 不匹配；不要从昵称猜测内部 wxid')


def state(root):
    return {rel + suffix: digest(p) if p.exists() else None
            for rel in ALL for suffix in ('', '-wal', '-shm')
            for p in [Path(root) / (rel + suffix)]}


def fresh(path):
    os.umask(0o077)
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=False, mode=0o700)
    return p


def _copy_snapshot(root, output):
    _single_shard(root)
    before = state(root)
    for rel in ALL:
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
    if state(root) != before:
        raise ValueError('复制期间微信库发生变化，请退出后使用新目录重试')
    return before


def export_root(root, keys, output):
    mac_only(); ensure_closed()
    root = Path(root).resolve(strict=True)
    output = Path(output).resolve()
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('导出目录必须位于微信数据目录之外')
    output = fresh(output)
    snapshot = output / '_encrypted_snapshot'
    before = _copy_snapshot(root, snapshot)
    # Keys can be bound to original absolute paths. Resolve them against originals
    # then bind to snapshot relative paths in a temporary private manifest.
    from .vendor.sqlcipher_probe import load_key
    copied_keys = output / '_snapshot_keys.json'
    private_json(copied_keys, {'keys': {rel: {'enc_key': load_key(keys, root / rel)} for rel in ALL}})
    try:
        for rel in ALL:
            export_database(snapshot / rel, output / rel, copied_keys)
    finally:
        copied_keys.unlink(missing_ok=True)
    if state(root) != before:
        raise ValueError('导出期间原库改变，当前导出不可用于恢复')
    report = {'version': 1, 'account_directory': root.parent.name, 'originals': before,
              'exported': {rel: digest(output / rel) for rel in ALL}}
    private_json(output / 'export-report.json', report)
    return {'report': str(output / 'export-report.json'), 'database_count': len(ALL)}


def make_plan(source, root, keys, owner, output):
    mac_only(); ensure_closed()
    root = Path(root).resolve(strict=True)
    check_owner(root, owner)
    output = fresh(output)
    # Candidate/data paths must never be within live db_storage.
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('计划目录必须位于微信数据目录之外')
    exported = output / 'target-plain'
    export_root(root, keys, exported)
    report = json.loads((exported / 'export-report.json').read_text())
    result = prepare_plain_restore(source, exported, owner, output / 'recovered-plain')
    for rel in RELS:
        encrypt_database(output / 'recovered-plain' / rel, output / 'candidates' / rel, root / rel, keys)
    ensure_closed()
    if state(root) != report['originals']:
        raise ValueError('生成计划期间目标库改变')
    plan = {'version': 1, 'target_root': str(root), 'target_owner': owner,
            'originals': report['originals'], 'candidates': {r: digest(output / 'candidates' / r) for r in RELS},
            'report': result}
    private_json(output / 'plan.json', plan)
    private_json(output / 'preview.json', {'target_owner': owner, **result})
    return {'preview': str(output / 'preview.json'), 'plan': str(output), **result}


def apply_plan(directory):
    mac_only(); ensure_closed()
    directory = Path(directory).resolve(strict=True)
    with (directory / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = json.loads((directory / 'plan.json').read_text())
        if plan.get('version') != 1 or set(plan['candidates']) != set(RELS):
            raise ValueError('计划格式不受支持')
        if (directory / 'apply-started.json').exists():
            raise ValueError('计划已尝试应用；请检查回执并重新生成计划')
        root = Path(plan['target_root']).resolve(strict=True)
        check_owner(root, plan['target_owner'])
        if directory.is_relative_to(root) or root.is_relative_to(directory):
            raise ValueError('计划与原库目录必须分离')
        allowed = {r + s for r in ALL for s in ('', '-wal', '-shm')}
        if set(plan['originals']) != allowed:
            raise ValueError('计划原件路径不受支持')
        for rel, h in plan['originals'].items():
            p = root / rel
            if p.is_symlink() or (digest(p) if p.exists() else None) != h:
                raise ValueError('原库或 WAL/SHM 已改变，拒绝应用')
        for rel, h in plan['candidates'].items():
            p = directory / 'candidates' / rel
            if p.is_symlink() or digest(p) != h:
                raise ValueError('候选已改变，拒绝应用')
        backup = directory / 'originals'
        backup.mkdir(mode=0o700)
        staged, replaced = [], []
        # Copy every original before the first replacement. WAL/SHM is essential.
        for rel, h in plan['originals'].items():
            if h is not None:
                q = backup / rel
                q.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / rel, q)
                q.chmod(0o600)
                if digest(q) != h:
                    raise ValueError('原件备份哈希不匹配')
        try:
            for rel in RELS:
                p = Path(str(root / rel) + '.chat-rescue-stage')
                if p.exists():
                    raise ValueError('发现未清理的暂存文件，请检查之前的操作')
                with p.open('xb') as f, (directory / 'candidates' / rel).open('rb') as src:
                    staged.append(p)
                    shutil.copyfileobj(src, f)
                    f.flush(); os.fsync(f.fileno())
                p.chmod(0o600)
            ensure_closed()
            for rel, h in plan['originals'].items():
                p = root / rel
                if (digest(p) if p.exists() else None) != h:
                    raise ValueError('应用前目标库改变')
            private_json(directory / 'apply-started.json', {'state': 'started', 'backup': str(backup)})
            for rel, p in zip(RELS, staged):
                # Mark before touching sidecars so rollback also repairs this DB.
                replaced.append(rel)
                for suffix in ('-wal', '-shm'):
                    Path(str(root / rel) + suffix).unlink(missing_ok=True)
                os.replace(p, root / rel)
            private_json(directory / 'applied.json', {'state': 'applied', 'backup': str(backup), **plan['report']})
        except BaseException:
            errors = []
            for rel in reversed(replaced):
                for suffix in ('', '-wal', '-shm'):
                    name = rel + suffix
                    try:
                        original, live = backup / name, root / name
                        if original.exists():
                            tmp = Path(str(live) + '.chat-rescue-rollback')
                            shutil.copy2(original, tmp); os.replace(tmp, live)
                        else:
                            live.unlink(missing_ok=True)
                    except OSError:
                        errors.append(name)
            if replaced:
                private_json(directory / 'rollback.json', {'state': 'rollback_failed' if errors else 'rolled_back', 'failed_paths': errors, 'backup': str(backup)})
            raise
        finally:
            for p in staged:
                p.unlink(missing_ok=True)
        return {'state': 'applied', 'receipt': str(directory / 'applied.json'), 'backup': str(backup), **plan['report']}
