"""Versioned private, local task transfer. Never copies a live account directory."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import uuid
from .runtime import make_private, require_private
from .workflow import digest, private_json


def _plain(path):
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError('任务资料不能包含符号链接或目录联接')
    return path


def _relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or value != str(PurePosixPath(value)) or PurePosixPath(value).is_absolute()
            or any(p in ('.', '..') or p.rstrip(' .') != p or
                   re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?', p)
                   for p in value.split('/'))):
        raise ValueError('任务相对路径不安全')
    return value


def _directory(workspace, task_id):
    if not isinstance(task_id, str) or not re.fullmatch('[0-9a-f]{32}', task_id):
        raise ValueError('任务标识无效')
    return _plain(Path(workspace) / 'tasks' / task_id)


def _write(directory, value):
    temporary = directory / ('index-' + uuid.uuid4().hex + '.json')
    private_json(temporary, value)
    temporary.replace(directory / 'task.json')


def create(workspace):
    value = {'version': 1, 'id': uuid.uuid4().hex, 'accounts': [{}, {}], 'errors': ['', ''],
             'failure_reports': ['', ''],
             'plan': '', 'history_plan': '', 'history_only': False, 'relocated': False,
             'include_voice': False, 'include_attachments': False}
    directory = _directory(workspace, value['id']); directory.mkdir(parents=True)
    make_private(directory); _write(directory, value)
    return value


def load(workspace, task_id):
    directory = _directory(workspace, task_id); require_private(directory / 'task.json')
    value = json.loads((directory / 'task.json').read_text(encoding='utf-8'))
    if value.get('version') != 1 or value.get('id') != task_id:
        raise ValueError('任务索引版本或标识不匹配')
    return value


def update(workspace, task_id, **fields):
    if set(fields) - {'accounts', 'errors', 'failure_reports', 'plan', 'include_voice', 'include_attachments'}:
        raise ValueError('不能修改任务来源或历史保护状态')
    value = load(workspace, task_id)
    if 'accounts' in fields and (not isinstance(fields['accounts'], list) or len(fields['accounts']) != 2):
        raise ValueError('任务必须包含两个账号槽位')
    value.update(fields); _write(_directory(workspace, task_id), value)
    return value


def _managed(workspace, source):
    source = _plain(Path(source).absolute()).resolve(strict=True)
    work = Path(workspace).resolve()
    if not any(source.is_relative_to(work / name) for name in ('jobs', 'tasks')):
        raise ValueError('任务只能包含当前工作区内显式关联的读取资料或预览')
    return source


def _files(root):
    root = _plain(root)
    result = {}; seen = set()
    for base, directories, filenames in os.walk(root, followlinks=False):
        for name in directories + filenames:
            _plain(Path(base) / name)
        for name in filenames:
            path = Path(base) / name
            if not path.is_file():
                raise ValueError('任务含非普通文件')
            rel = _relative(path.relative_to(root).as_posix())
            if rel.casefold() in seen:
                raise ValueError('任务文件大小写冲突')
            seen.add(rel.casefold())
            result[rel] = digest(path)
    return result


def export_task(workspace, task_id, output):
    value = load(workspace, task_id)
    output = _plain(Path(output).absolute())
    work = Path(workspace).resolve()
    if output.resolve().is_relative_to(work) or work.is_relative_to(output.resolve()):
        raise ValueError('请将接续目录放在工作区之外的独立新目录')
    sources = {}
    accounts = []
    failures = ['', '']
    for i, source in enumerate(value.get('failure_reports', ['', ''])):
        if source:
            path = _managed(workspace, source); require_private(path)
            if path.name != 'keys.capture-failed.json':
                raise ValueError('只允许明确的脱敏读取失败报告')
            rel = 'artifacts/failures/' + str(i) + '/keys.capture-failed.json'
            sources[rel] = path; failures[i] = rel
    for i, item in enumerate(value['accounts']):
        entry = {k: str(item.get(k, '')) for k in ('root','owner','nickname')}
        entry['keys'] = ''
        if item.get('keys'):
            key = _managed(workspace, item['keys'])
            for path in (key, key.with_suffix('.capture.json')):
                _managed(workspace, path)
                require_private(path)
                sources['artifacts/accounts/' + str(i) + '/' + path.name] = path
            entry['keys'] = 'artifacts/accounts/' + str(i) + '/' + key.name
        accounts.append(entry)
    # Each source tree is an explicitly indexed preview job, never db_storage.
    plan = value.get('plan') or value.get('history_plan')
    inventory = None; root = None
    if plan:
        # Failed planning may have private schema diagnostics but no sync-plan.
        candidate = Path(plan)
        if not candidate.exists():
            _managed(workspace, candidate.parent)
            root = None
        else:
            root = _managed(workspace, plan)
            if (root/'sync-plan.json').is_file():
                previous=json.loads((root/'sync-plan.json').read_text(encoding='utf-8')).get('accounts',{})
                for i,slot in enumerate(('A','B')):
                    if previous.get(slot,{}).get('owner') and previous[slot]['owner']!=value['accounts'][i].get('owner'):
                        raise ValueError('当前账号选择已改变，不能把旧账号预览混入本次导出；请新建任务生成新预览，或载入原任务')
        inventory = _files(root) if root is not None else {}
        for rel in inventory:
            sources['artifacts/plan/' + rel] = root / rel
    if not sources and not any(value.get('errors', [])):
        raise ValueError('空任务没有可接续资料')
    output.mkdir(parents=True, exist_ok=False); make_private(output)
    hashes = {}
    for rel, source in sources.items():
        before = digest(source); target = output / _relative(rel)
        target.parent.mkdir(parents=True, exist_ok=True); make_private(target.parent)
        with source.open('rb') as reader, target.open('xb') as writer:
            make_private(target); shutil.copyfileobj(reader, writer, 1024*1024)
        if digest(source) != before or digest(target) != before:
            raise ValueError('导出期间任务资料改变，请保留现场并重新导出到新目录')
        hashes[rel] = before
    if root is not None and inventory is not None and _files(root) != inventory:
        raise ValueError('导出期间任务文件清单改变')
    payload = {'version': 1, 'kind': 'chat-rescue-private-task', 'files': hashes,
               'task': {**value, 'accounts': accounts, 'failure_reports': failures, 'plan': 'artifacts/plan' if inventory else '',
                        'history_plan': '', 'history_only': True, 'relocated': True}}
    private_json(output / 'transfer.json', payload)
    return {'path': str(output), 'file_count': len(hashes), 'state': 'exported'}


def import_task(workspace, path):
    path = _plain(Path(path).absolute())
    manifest = path / 'transfer.json'
    if manifest.stat().st_size > 8*1024*1024:
        raise ValueError('接续清单过大')
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    if payload.get('version') != 1 or payload.get('kind') != 'chat-rescue-private-task':
        raise ValueError('这不是受支持的私人任务接续目录')
    files = payload.get('files')
    if not isinstance(files, dict) or len(files) > 100000:
        raise ValueError('接续文件清单无效')
    folded = set()
    for rel, hash_value in files.items():
        _relative(rel)
        if (not rel.startswith('artifacts/') or rel.casefold() in folded
                or not isinstance(hash_value, str) or not re.fullmatch('[0-9a-f]{64}', hash_value)):
            raise ValueError('接续文件清单存在冲突')
        folded.add(rel.casefold())
    actual = _files(path); actual.pop('transfer.json', None)
    if actual != files:
        raise ValueError('接续文件缺失、多余或哈希不匹配')
    old = payload.get('task', {})
    if (not isinstance(old.get('accounts'), list) or len(old['accounts']) != 2
            or not isinstance(old.get('errors'), list) or len(old['errors']) != 2):
        raise ValueError('接续账号索引无效')
    for i, item in enumerate(old['accounts']):
        if not isinstance(item, dict):
            raise ValueError('接续账号格式无效')
        key = item.get('keys')
        if key and (key not in files or not key.startswith('artifacts/accounts/' + str(i) + '/')
                    or str(PurePosixPath(key).with_suffix('.capture.json')) not in files):
            raise ValueError('接续密钥与回执缺失或串号')
    if old.get('plan') not in ('', 'artifacts/plan'):
        raise ValueError('接续预览路径无效')
    if old.get('plan') and not any(r.startswith('artifacts/plan/') for r in files):
        raise ValueError('接续预览或失败诊断缺失')
    failures=old.get('failure_reports',['',''])
    if not isinstance(failures,list) or len(failures)!=2 or any(
            rel and (rel not in files or rel!='artifacts/failures/'+str(i)+'/keys.capture-failed.json')
            for i,rel in enumerate(failures)):
        raise ValueError('接续失败报告索引无效')
    value = create(workspace); directory = _directory(workspace, value['id'])
    # Guard before copying any historical candidate; interrupted copies stay read-only.
    private_json(directory / 'migration-history.json', {'version':1, 'old_candidates_read_only':True})
    for rel, expected in files.items():
        target = directory / rel; target.parent.mkdir(parents=True, exist_ok=True); make_private(target.parent)
        with (path / rel).open('rb') as reader, target.open('xb') as writer:
            make_private(target); shutil.copyfileobj(reader, writer, 1024*1024)
        if digest(target) != expected:
            raise ValueError('载入期间接续文件改变，未建立可执行任务')
    accounts = []
    for item in old['accounts']:
        accounts.append({k: str(item.get(k, '')) for k in ('root','owner','nickname','keys')})
        if item.get('keys'): accounts[-1]['keys'] = str(directory / item['keys'])
    value.update(accounts=accounts, errors=old['errors'], relocated=True, history_only=True,
                 failure_reports=[str(directory/rel) if rel else '' for rel in failures],
                 include_voice=old.get('include_voice') is True, include_attachments=old.get('include_attachments') is True,
                 history_plan=str(directory / 'artifacts/plan') if old.get('plan') else '',
                 origin_task_id=str(old.get('id', '')))
    _write(directory, value)
    return value


def historical_plan(path):
    directory=Path(path).resolve()
    return any((parent / 'migration-history.json').is_file() for parent in (directory,*directory.parents))


def require_current_plan(path):
    if historical_plan(path):
        raise ValueError('迁入的历史候选仅供查看，不能应用或撤回；请重新定位核验本机账号，并为本机生成新预览')
