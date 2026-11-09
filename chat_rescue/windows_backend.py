"""Local stdin/stdout JSON bridge for the Windows five-step desktop wizard."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid
from . import __main__ as cli
from .operations import ALL, check_owner, ensure_closed, fresh
from .runtime import make_private, require_private
from .workflow import connect, digest, private_json, DirectionMismatch
from .sync import make_sync_plan, apply_sync_plan, undo_sync_plan
from .replacement import make_replace_plan
from .windows_capture import app_info, capture
from .vendor.sqlcipher_probe import DB, load_key
from .cipher import literal
from .shards import database_rels, receipt_covers, check_paths
from . import portable_task
from .vendor.capture_keys import verified as _page_verified
from .windows_version import is_windows_42_version


def workspace():
    override = os.environ.get('CHAT_RESCUE_WORKSPACE', '')
    if override and not Path(override).is_absolute():
        raise ValueError('桌面工作目录必须使用绝对路径')
    base = Path(override) if override else Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'ChatRescue/Desktop'
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    make_private(base)
    return base.resolve()


def job():
    return fresh(workspace() / 'jobs' / uuid.uuid4().hex)


def checked_app(executable):
    manifest_path = os.environ.get('CHAT_RESCUE_CLIENT_MANIFEST', '')
    if not manifest_path:
        return app_info(executable)
    from .windows_client import manifest, validate
    expected = os.environ.get('CHAT_RESCUE_CLIENT_SHA256', '')
    value = manifest(manifest_path, expected)
    root = Path(manifest_path).parent / 'wechat'
    pinned = (root / value['executable']).resolve()
    if Path(executable).resolve() != pinned:
        raise ValueError('此版本只使用包内固定微信，请重新准备；不能切换到其他程序')
    return validate(root, manifest_path, expected)


def saved_settings():
    path = workspace() / 'settings.json'
    if not path.exists():
        return {}
    require_private(path)
    return json.loads(path.read_text(encoding='utf-8'))


def save_settings(request):
    values = request.get('accounts')
    if not isinstance(values, list) or len(values) != 2:
        raise ValueError('桌面账号设置格式不受支持')
    payload = {'executable': str(request.get('executable', '')),
               'accounts': [{key: str(value.get(key, '')) for key in ('owner', 'root', 'keys', 'nickname')}
                            for value in values]}
    directory = workspace()
    temporary = directory / ('settings-' + uuid.uuid4().hex + '.json')
    private_json(temporary, payload)
    temporary.replace(directory / 'settings.json')
    return {'state': 'saved'}


def find_executable():
    if os.name != 'nt':
        return ''
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r'Software\Tencent\Weixin') as key:
                base = Path(winreg.QueryValueEx(key, 'InstallPath')[0])
                if (base / 'Weixin.exe').is_file():
                    return str(base / 'Weixin.exe')
        except OSError:
            pass
    return ''


def normalize_account_directory(value):
    path=Path(value)
    if not path.is_absolute():
        raise ValueError('请选择账号 db_storage 的完整绝对路径')
    if any(p.is_symlink() or p.is_junction() for p in (path,*path.parents)):
        raise ValueError('账号目录不能使用符号链接或目录联接，请选择实际 db_storage 路径')
    note=''
    if path.name.casefold()=='db_storage':
        root=path
    elif path.name.casefold()=='message' and path.parent.name.casefold()=='db_storage':
        root=path.parent;note='已从 message 子目录返回父级 db_storage；仍需检查所有必要库'
    elif (path/'db_storage').is_dir():
        root=path/'db_storage';note='已选择该账号目录下唯一的 db_storage；仍需检查所有必要库'
    else:
        raise ValueError('目录层级不正确：请选择 xwechat_files\\某账号目录\\db_storage；选择其 message 子目录可返回父级，不能选择包含多个账号的 xwechat_files 或文档根目录')
    if root.is_symlink() or root.is_junction():
        raise ValueError('请选择实际 db_storage，不能通过链接规范化账号路径')
    return root.resolve(),note


def indexed_account_hints():
    """Read explicit workspace indexes only; never open a key or chat database."""
    def read(path):
        try:
            if path.is_symlink() or path.is_junction() or path.stat().st_size>4*1024*1024:return {}
            require_private(path)
            value=json.loads(path.read_text(encoding='utf-8'))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError,UnicodeError):
            return {}
    records=[]
    registry=read(workspace()/'account-material.json')
    records.extend((0,item) for item in registry.values() if isinstance(item,dict))
    for path in sorted((workspace()/'tasks').glob('*/task.json')):
        if not re.fullmatch('[0-9a-f]{32}',path.parent.name):continue
        if path.parent.is_symlink() or path.parent.is_junction():continue
        value=read(path)
        if value.get('version')!=1 or value.get('id')!=path.parent.name:continue
        items=value.get('accounts',[])
        if isinstance(items,list):records.extend((1,item) for item in items if isinstance(item,dict))
    try:items=saved_settings().get('accounts',[])
    except (OSError,ValueError,UnicodeError):items=[]
    if isinstance(items,list):records.extend((2,item) for item in items if isinstance(item,dict))
    result={}
    for priority,item in records:
        if not isinstance(item.get('root'),str) or not item['root']:continue
        try:root,_=normalize_account_directory(item['root'])
        except (OSError,ValueError):continue
        owner=re.sub(r'_[0-9a-fA-F]{4}$','',root.parent.name)
        if item.get('owner') and item['owner']!=owner:continue
        key=os.path.normcase(str(root));entry=result.setdefault(key,{'root':str(root),'names':{},'owner':owner})
        nickname=item.get('nickname')
        if isinstance(nickname,str) and nickname.strip():
            entry['names'].setdefault(priority,set()).add(nickname.strip())
    for entry in result.values():
        groups=entry.pop('names');entry['nickname_hints']=sorted(groups[min(groups)]) if groups else []
        entry['nickname']=entry['nickname_hints'][0] if len(entry['nickname_hints'])==1 else ''
    return result


def account_directory_info(value, hint=None):
    root,note=normalize_account_directory(value)
    hint=hint or {};nickname=hint.get('nickname','');aliases=hint.get('nickname_hints',[])
    missing=[rel for rel in ALL if not (root/rel).is_file()]
    error=''
    try:
        if any((root/rel).is_symlink() or (root/rel).is_junction() or (root/rel).parent.is_symlink()
               or (root/rel).parent.is_junction() for rel in ALL):
            raise ValueError('必要数据库或目录不能使用链接')
        if root.exists():database_rels(root)
    except ValueError as failure:error=str(failure)
    state=('missing_directory' if not root.is_dir() else 'missing_databases' if missing else
           'invalid_inventory' if error else 'historical' if nickname or aliases else 'unidentified')
    label=(nickname+' · 上次识别' if nickname else '历史昵称不一致 · 待核验' if aliases else '未识别账号 · '+root.parent.name)
    if state=='missing_directory':label+=' · 路径已失效'
    elif state=='missing_databases':label+=' · 目录缺库'
    elif state=='invalid_inventory':label+=' · 分片清单需检查'
    return {'root':str(root),'owner':re.sub(r'_[0-9a-fA-F]{4}$','',root.parent.name),
            'nickname':nickname,'nickname_hints':aliases,'label':label,'state':state,
            'verified':False,'complete':not missing and not error and root.is_dir(),
            'missing_databases':missing,'inventory_error':error,'normalization':note}


def discover():
    bases = set()
    config = Path(os.environ.get('APPDATA', Path.home())) / 'Tencent/xwechat/config'
    for path in config.glob('*.ini'):
        try:
            if path.is_symlink() or path.is_junction() or path.stat().st_size>4096:continue
            value = path.read_text(encoding='utf-8-sig').strip().rstrip('\0')
            if value and Path(value).is_absolute():
                base = Path(value)
                bases.add(base if base.name == 'xwechat_files' else base / 'xwechat_files')
        except (OSError, UnicodeError, ValueError):
            continue
    found = set(cli.roots()['database_roots'])
    for base in bases:
        found.update(str(p.resolve()) for p in base.glob('*/db_storage'))
    hints=indexed_account_hints();found.update(entry['root'] for entry in hints.values())
    candidates={}
    for value in sorted(found):
        try:
            root,_=normalize_account_directory(value);key=os.path.normcase(str(root))
            candidates[key]=account_directory_info(str(root),hints.get(key))
        except (OSError,ValueError):continue
    values=list(candidates.values())
    counts={}
    for item in values:
        if item['nickname']:counts[item['nickname']]=counts.get(item['nickname'],0)+1
    for item in values:
        if counts.get(item['nickname'],0)>1:item['label']+=' · '+item['root']
    return values


def resource_processes(root):
    """Query file users, never shuts down or restarts a process."""
    class Unique(C.Structure):
        _fields_ = [('pid', W.DWORD), ('start', W.FILETIME)]
    class Info(C.Structure):
        _fields_ = [('process', Unique), ('name', W.WCHAR * 256), ('service', W.WCHAR * 64),
                    ('kind', W.DWORD), ('status', W.DWORD), ('session', W.DWORD), ('restartable', W.BOOL)]
    dll = C.WinDLL('rstrtmgr')
    dll.RmStartSession.argtypes = [C.POINTER(W.DWORD), W.DWORD, W.LPWSTR]
    dll.RmRegisterResources.argtypes = [W.DWORD, W.UINT, C.POINTER(W.LPCWSTR), W.UINT, C.c_void_p, W.UINT, C.c_void_p]
    dll.RmGetList.argtypes = [W.DWORD, C.POINTER(W.UINT), C.POINTER(W.UINT), C.POINTER(Info), C.POINTER(W.DWORD)]
    dll.RmEndSession.argtypes = [W.DWORD]
    session, key = W.DWORD(), C.create_unicode_buffer(33)
    if dll.RmStartSession(C.byref(session), 0, key):
        raise RuntimeError('无法查询账号数据库占用进程')
    try:
        rels = tuple(rel + suffix for rel in database_rels(root)
                     for suffix in ('', '-wal', '-shm'))
        check_paths(root, rels)
        paths = [Path(root) / rel for rel in rels if (Path(root) / rel).is_file()]
        files = (W.LPCWSTR * len(paths))(*(str(path) for path in paths))
        if dll.RmRegisterResources(session, len(paths), files, 0, None, 0, None):
            raise RuntimeError('无法登记账号数据库查询')
        needed, count, reasons = W.UINT(), W.UINT(64), W.DWORD()
        infos = (Info * 64)()
        if dll.RmGetList(session, C.byref(needed), C.byref(count), infos, C.byref(reasons)):
            raise RuntimeError('账号数据库进程查询未完成')
        return sorted({infos[i].process.pid for i in range(count.value)})
    finally:
        dll.RmEndSession(session)


def main_processes(executable):
    script = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
              "Import-Module (Join-Path $PSHOME 'Modules/CimCmdlets/CimCmdlets.psd1') -ErrorAction Stop; "
              "@(Get-CimInstance Win32_Process -Filter \"Name='Weixin.exe' OR Name='WeChat.exe'\" | "
              "Where-Object {$_.ExecutablePath -eq $env:CHAT_RESCUE_WECHAT -and $_.CommandLine -notmatch '--type='} | "
              "ForEach-Object {[int]$_.ProcessId}) | ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env=dict(os.environ, CHAT_RESCUE_WECHAT=str(Path(executable).resolve())),
                            check=True, capture_output=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    data = json.loads(result.stdout or '[]')
    return [data] if isinstance(data, int) else data or []


def account(value):
    result = {key: str(value.get(key, '')) for key in ('owner', 'root', 'keys')}
    info=account_directory_info(result['root']);root=Path(info['root'])
    if not result['owner']:
        raise ValueError('请先选择并核对账号')
    check_owner(root, result['owner'])
    if not info['complete']:
        raise ValueError('所选 db_storage 未就绪：'+('缺少 '+ '、'.join(info['missing_databases']) if info['missing_databases'] else info['inventory_error'])+'；请核对账号和目录层级，不能用 message 子目录代替整套库')
    result['root'] = str(root)
    return result


def account_nickname(selected, keys):
    contact = Path(selected['root']) / 'contact/contact.db'
    with DB(contact, load_key(keys, contact)) as database:
        rows = database.query('SELECT username,nick_name FROM contact WHERE username=' + literal(selected['owner']))
    if len(rows) != 1:
        raise ValueError('四库密钥已保存，但未能唯一核对本人联系人身份；请保留任务回执')
    return rows[0]['nick_name'] or '未设置昵称'


def verify_page_hmac(root, rel, key):
    with (Path(root) / rel).open('rb') as stream:
        page = stream.read(4096)
    if not _page_verified(bytes.fromhex(key), page):
        raise ValueError('必要数据库 HMAC 验证失败：' + rel)


def reverify_material(selected, include_voice=False):
    """Validate each current key; old inventory is provenance, not fresh proof."""
    from .operations import inspection_files
    path = Path(selected['keys']); require_private(path)
    receipt = path.with_suffix('.capture.json'); require_private(receipt)
    metadata = json.loads(receipt.read_text(encoding='utf-8'))
    if metadata.get('state') != 'verified':
        raise ValueError('已有回执不是有效的已验证记录，不能复用；请打开微信并登录本次账号后获取新密钥')
    if metadata.get('owner') != selected['owner']:
        raise ValueError('旧回执与所选账号身份不匹配，不能复用；请核对账号目录，或打开微信并登录所选账号后获取新密钥')
    if not is_windows_42_version(metadata.get('file_version')):
        raise ValueError('旧回执缺少受支持的完整Windows版本信息；请打开微信并登录本次账号后获取新密钥')
    rels = inspection_files(selected['root'], include_voice)
    payload = json.loads(path.read_text(encoding='utf-8-sig'))
    entries = payload.get('keys', payload)
    if not isinstance(entries, dict):
        raise ValueError('密钥清单格式无效')
    keys = {}; unavailable=[]
    for rel in rels:
        old_path = str(metadata.get('database_root', '')).replace('\\','/').rstrip('/') + '/' + rel
        matches = []
        for name, value in entries.items():
            if name.replace('\\','/') in (rel, old_path):
                key = value.get('enc_key', value.get('key')) if isinstance(value, dict) else value
                if isinstance(key, str) and re.fullmatch('[0-9a-fA-F]{64}',key): matches.append(key.lower())
        if not matches:
            try:
                matches = [load_key(path, Path(selected['root']) / rel)]
            except RuntimeError:
                unavailable.append(rel);continue
        if len(set(matches)) != 1:
            raise ValueError('必要库密钥存在冲突：' + rel)
        keys[rel] = matches[0]
        try:
            verify_page_hmac(selected['root'],rel,keys[rel])
        except ValueError:
            raise ValueError('历史密钥未通过本次数据库 HMAC：'+rel+'。请核对所选账号目录；确认目录正确后，先点“打开微信并登录账号”，本人登录该账号后再读取，将重新取钥') from None
    if unavailable:
        raise ValueError('历史密钥清单缺少或无法唯一匹配当前库：'+'、'.join(unavailable)+
                         '。请先点“打开微信并登录账号”，本人登录所选账号后再点读取，将获取当前全部必要库密钥；旧回执和另一账号结果保留')
    return metadata, keys, digest(receipt)


def reverify_account(selected, include_voice=False):
    ensure_closed()
    metadata, keys, previous_hash = reverify_material(selected,include_voice)
    directory = job(); pending = directory / 'keys.pending.json'
    private_json(pending, {'keys': {rel: {'enc_key':key} for rel,key in keys.items()}})
    verification = cli.verify_keys(selected['root'],pending, **({'include_voice':True} if include_voice else {}))
    nickname = account_nickname(selected,pending)
    output = directory / 'keys.json'; pending.replace(output)
    receipt = output.with_suffix('.capture.json')
    private_json(receipt, {**{k:v for k,v in metadata.items() if k not in ('pid','database_root')},
                 **verification, 'state':'verified','database_root':str(Path(selected['root']).resolve()),
                 'owner':selected['owner'],'relocated_from_receipt_sha256':previous_hash})
    return {**selected,'keys':str(output),'nickname':nickname}


def record_read(request, selected=None, error='', failure_report=''):
    if selected is not None:
        # One current verified-material reference per identity+directory, independent
        # of which source/target role the user happens to choose in this task.
        path=workspace()/'account-material.json'
        registry={}
        if path.exists():
            require_private(path);registry=json.loads(path.read_text(encoding='utf-8'))
        root=str(Path(selected['root']).resolve())
        identity=selected['owner']+'\0'+os.path.normcase(root)
        registry[identity]={k:str(selected.get(k,'')) for k in ('root','owner','keys','nickname')}
        temporary=workspace()/('account-material-'+uuid.uuid4().hex+'.json')
        private_json(temporary,registry);temporary.replace(path)
    if not request.get('task_id'):
        return
    task=portable_task.load(workspace(),request['task_id'])
    index=1 if request.get('slot')=='B' else 0
    if selected is not None: task['accounts'][index]=selected
    task['errors'][index]=error
    reports=task.get('failure_reports',['','']); reports[index]=failure_report
    portable_task.update(workspace(),task['id'],accounts=task['accounts'],errors=task['errors'],failure_reports=reports)


def cached_keys(selected):
    """Only explicitly indexed material for this exact chosen identity/root."""
    registry_path=workspace()/'account-material.json'
    values=[]
    if registry_path.exists():
        require_private(registry_path)
        registry=json.loads(registry_path.read_text(encoding='utf-8'))
        identity=selected['owner']+'\0'+os.path.normcase(str(Path(selected['root']).resolve()))
        if identity in registry:
            values.append(registry[identity])
    if not values:
        values=list(saved_settings().get('accounts', []))
    # Old tasks are a compatibility source only when there is no current account
    # reference. A damaged unrelated index must not break a fresh selection.
    for path in (() if registry_path.exists() and values else (workspace()/'tasks').glob('*/task.json')):
        try:
            require_private(path)
            values.extend(portable_task.load(workspace(),path.parent.name)['accounts'])
        except (OSError,ValueError,KeyError,json.JSONDecodeError):
            continue
    found=set()
    for item in values:
        if (item.get('owner')==selected['owner'] and item.get('root')
                and Path(item['root']).resolve()==Path(selected['root']).resolve() and item.get('keys')):
            path=Path(item['keys'])
            if path.is_file() and path.with_suffix('.capture.json').is_file(): found.add(str(path.resolve()))
    if len(found)>16:
        raise ValueError('此目录历史密钥候选过多，请显式导入本次配套密钥后重新核验')
    return sorted(found)


def plan_view(directory):
    directory = Path(directory).resolve(strict=True)
    manifest = json.loads((directory / 'sync-plan.json').read_text(encoding='utf-8'))
    if (manifest.get('version') not in (1, 4, 5) or set(manifest.get('accounts', {})) != {'A', 'B'}
            or (manifest.get('version') in (4,5) and manifest.get('mode') != 'replace-a-from-b')):
        raise ValueError('这不是受支持的双向计划')
    if manifest['version']==5:
        from .media import windows_options
        caps=manifest.get('capabilities',[])
        windows_options({**manifest,'capabilities':caps[:-2]})
        if caps[-2:]!=['windows-single-chat-replacement','windows-verified-shard-routing']:
            raise ValueError('媒体替换计划缺少已验证分片能力')
    receipt = directory / 'sync-result.json'
    state = json.loads(receipt.read_text(encoding='utf-8'))['state'] if receipt.exists() else 'ready'
    if (directory / 'sync-started.json').exists() and not receipt.exists():
        state = 'interrupted'
    if (directory / 'undo-result.json').exists():
        state = 'undone'
    plan_hash = digest(directory / 'sync-plan.json')
    confirmed = directory / 'client-confirmed.json'
    client_confirmed = False
    if confirmed.exists():
        confirmation = json.loads(confirmed.read_text(encoding='utf-8'))
        client_confirmed = confirmation.get('state') == 'user_confirmed' and confirmation.get('plan_hash') == plan_hash and state == 'applied'
    return {'plan': str(directory), 'plan_hash': plan_hash, 'client_confirmed': client_confirmed,
            'history_only': portable_task.historical_plan(directory),
            'accounts': manifest['accounts'], 'directions': manifest['directions'], 'state': state,
            'mode': manifest.get('mode', 'sync')}


def guarded_plan(request):
    view = plan_view(request['plan'])
    if view.get('history_only'):
        raise ValueError('迁入的旧预览仅供查看，请重新核验本机账号并生成新任务预览；原回执保留')
    if not request.get('plan_hash') or view['plan_hash'] != request['plan_hash']:
        raise ValueError('计划已变化，请重新载入并核对预览')
    return view


def dispatch(request):
    action = request.get('action')
    media_options = {key: True for key in ('include_voice','include_attachments') if request.get(key) is True}
    if action == 'task-new':
        return portable_task.create(workspace())
    if action == 'task-load':
        return portable_task.load(workspace(),request['task_id'])
    if action == 'task-save':
        return portable_task.update(workspace(),request['task_id'],
                  accounts=request['accounts'], errors=request.get('errors',['','']),
                  include_voice=bool(media_options.get('include_voice')),include_attachments=bool(media_options.get('include_attachments')))
    if action == 'task-export':
        return portable_task.export_task(workspace(),request['task_id'],request['output'])
    if action == 'task-import':
        return portable_task.import_task(workspace(),request['path'])
    if action == 'task-from-plan':
        view=plan_view(request['plan'])
        try:
            portable_task._managed(workspace(),view['plan'])
        except (ValueError,OSError):
            return {'task':{},'view':view}
        task=portable_task.create(workspace())
        values=[{**view['accounts'][slot],'keys':''} for slot in ('A','B')]
        task=portable_task.update(workspace(),task['id'],accounts=values,plan=view['plan'])
        return {'task':task,'view':view}
    if action == 'inspect-account-directory':
        root,_=normalize_account_directory(request['root'])
        return account_directory_info(str(request['root']),indexed_account_hints().get(os.path.normcase(str(root))))
    if action == 'status':
        return {'doctor': cli.doctor(), 'executable': find_executable(), 'candidates': discover(),
                'workspace': str(workspace()), 'platform': 'Windows', 'settings': saved_settings()}
    if action == 'save-settings':
        return save_settings(request)
    if action == 'prepare':
        metadata = checked_app(request['executable'])
        return {'doctor': cli.doctor(), 'app': metadata, 'workspace': str(workspace())}
    if action == 'launch':
        slot = request.get('slot')
        if slot not in ('A', 'B'):
            raise ValueError('请选择目标账号或来源账号的登录入口')
        metadata = checked_app(request['executable'])
        ensure_closed()
        process = subprocess.Popen([metadata['executable']], cwd=str(Path(metadata['executable']).parent),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'slot': slot, 'pids': [process.pid],
                'instruction': ('请本人登录所选来源账号，必要时从来源账号手机导入与目标账号的聊天；'
                                '电脑已有完整记录时可直接核对，确认后读取来源账号。')
                               if slot == 'B' else '请本人登录所选目标账号，再点击“读取目标账号”。来源和目标可按任意顺序读取。'}
    if action == 'capture':
        if os.environ.get('CHAT_RESCUE_CLIENT_MANIFEST'):
            checked_app(request['executable'])
        if request.get('slot') == 'B' and request.get('source_history_confirmed') is not True:
            raise ValueError('请先完成导入或确认电脑 B 已有完整记录，并核对与 A 的聊天后再读取 B')
        selected = account(request['account'])
        if request.get('identity_confirmed') is not True:
            raise ValueError('请确认本次账号归属后再读取')
        main = set(main_processes(request['executable']))
        if len(main)==1 and request.get('task_id'):
            candidates=[selected['keys']] if selected.get('keys') else cached_keys(selected)
            for key_path in candidates:
                try:
                    reverify_material({**selected,'keys':key_path},bool(media_options.get('include_voice')))
                except (OSError,ValueError,RuntimeError):
                    continue
                raise ValueError('已找到与所选账号目录 HMAC 匹配的历史密钥，无需重新取钥。请从微信菜单正常退出整个微信后，再点击读取，完成全部库完整性与本人昵称核验')
        if not main and not selected.get('keys') and request.get('task_id'):
            errors=[]
            for key_path in cached_keys(selected):
                try:
                    identified=reverify_account({**selected,'keys':key_path},bool(media_options.get('include_voice')))
                except (OSError,ValueError,RuntimeError) as error:
                    errors.append(str(error));continue
                record_read(request,identified)
                return {'state':'verified','account':identified,'reused_local_material':True}
            if errors:
                raise ValueError('已找到所选目录的历史密钥，但本次重新核验未通过：'+errors[-1])
        if not main and selected.get('keys'):
            ensure_closed()
            require_private(selected['keys'])
            if request.get('task_id'):
                selected = reverify_account(selected, bool(media_options.get('include_voice')))
                record_read(request,selected)
                return {'state':'verified','account':selected}
            cli.verify_keys(selected['root'], selected['keys'], **({'include_voice':True} if media_options.get('include_voice') else {}))
            return {'state': 'verified', 'account': {**selected,
                    'nickname': account_nickname(selected, selected['keys'])}}
        if not main:
            raise ValueError('匹配主进程 0 个：未找到所选程序的已运行微信，且没有可直接核验的密钥。请登录本次账号，或导入配套密钥；若微信已打开，请检查第一步的程序路径是否为当前使用的本机微信。所选程序：'+str(request['executable']))
        if len(main)>1:
            raise ValueError('匹配主进程 '+str(len(main))+' 个，PID：'+', '.join(map(str,sorted(main)))+'。请从微信菜单正常退出多余实例，只保留本次登录窗口；账号由全库验证，不按文件占用绑定')
        directory = job()
        try:
            result = capture(next(iter(main)), request['executable'], selected['root'], selected['owner'], directory / 'keys.json',
                             **({'include_voice':True} if media_options.get('include_voice') else {}))
            identified={**selected,'keys':result['keys'],'nickname':account_nickname(selected,result['keys'])}
            record_read(request,identified)
            return {**result,'account':identified}
        except (OSError,ValueError,RuntimeError) as error:
            report=directory/'keys.capture-failed.json'
            record_read(request,error=str(error),failure_report=str(report) if report.exists() else '')
            raise
    if action == 'plan':
        ensure_closed()
        if request.get('identity_confirmed') is not True:
            raise ValueError('请先核对双方账号身份')
        if request.get('source_history_confirmed') is not True:
            raise ValueError('请先确认手机 B 的所需聊天已导入或完整保存在电脑 B，并重新读取核验')
        a, b = account(request['a']), account(request['b'])
        if request.get('task_id'):
            task = portable_task.load(workspace(), request['task_id'])
            old = task.get('history_plan') or task.get('plan')
            if old and any((Path(old)/name).exists() for name in ('sync-started.json','sync-result.json','undo-result.json')):
                raise ValueError('此任务已有应用或中断历史，请保留回执并显式新建恢复任务')
        for selected in (a, b):
            require_private(selected['keys'])
        directory = job() / 'sync'
        if request.get('task_id'):
            portable_task.update(workspace(),request['task_id'],plan=str(directory))
        make_replace_plan(a, b, directory, **media_options)
        return plan_view(directory)
    if action == 'resume':
        return plan_view(request['plan'])
    if action == 'apply':
        view = guarded_plan(request)
        if request.get('confirmed') is not True:
            raise ValueError('请确认这份具体预览后再应用')
        if view['state'] != 'ready':
            raise ValueError('此任务已有回执或中断标记，不能再次应用')
        if view.get('mode')=='replace-a-from-b' and not view['directions']['A'].get('target_shard_imports'):
            raise ValueError('旧替换计划未验证分片分配，请保留任务并用新版重新生成预览')
        return apply_sync_plan(view['plan'])
    if action == 'undo':
        view = guarded_plan(request)
        if view['state'] != 'applied' or request.get('confirmed') is not True:
            raise ValueError('只能确认撤回已全部应用的本次任务')
        return undo_sync_plan(view['plan'])
    if action == 'confirm-client':
        view = guarded_plan(request)
        if view['state'] != 'applied' or request.get('confirmed') is not True:
            raise ValueError('只有数据库已应用且本人核对后才能记录电脑验收')
        private_json(Path(view['plan']) / 'client-confirmed.json', {'state': 'user_confirmed',
                     'plan_hash': view['plan_hash'], 'phone_verified': False})
        return {'state': 'user_confirmed', 'phone_verified': False}
    if action == 'import-accounts':
        path = Path(request['path']).resolve(strict=True)
        require_private(path)
        payload = json.loads(path.read_text(encoding='utf-8'))
        values = payload.get('accounts')
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError('请选择包含两个已验证账号的 verified-accounts.json')
        output = []
        for item in values:
            # Receipts bind paths; never return key data through the bridge.
            if item.get('slot') not in ('account-1', 'account-2'):
                raise ValueError('账号任务槽位不受支持')
            selected = account({**item, 'keys': str(path.parent / item['slot'] / 'keys.json')})
            receipt = json.loads((Path(selected['keys']).with_suffix('.capture.json')).read_text(encoding='utf-8'))
            if receipt.get('state') != 'verified' or not receipt_covers(selected['root'], receipt) or receipt.get('owner') != selected['owner'] or Path(receipt['database_root']).resolve() != Path(selected['root']):
                raise ValueError('账号与取钥回执不匹配')
            require_private(selected['keys'])
            own = next((c for c in item.get('contacts', []) if c['username'] == selected['owner']), {})
            output.append({**selected, 'nickname': own.get('nick_name') or '未设置昵称'})
        return {'accounts': output, 'instruction': '已载入先前四库取钥回执；仍需本人核对身份并重新生成计划。'}
    if action == 'simulation-report':
        path = Path(request['path']).resolve(strict=True)
        require_private(path)
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('simulation_only') is not True:
            raise ValueError('这不是隔离模拟报告')
        experiment = payload.get('prepared_experiment', {})
        if experiment.get('state') != 'passed':
            raise ValueError('这份隔离试验尚未通过，不能显示为成功结果；请查看本机报告')
        return {'simulation_only': True, 'state': experiment.get('state'), 'added': experiment.get('added', 0),
                'skipped': experiment.get('deduplicated_on_repeat', 0),
                'raw_empty_target': payload.get('raw_empty_target', {}).get('state'),
                'empty_target_uninitialized': 'Name2Id' in str(payload.get('raw_empty_target', {}).get('error', '')),
                'originals_unchanged': all(payload.get('original_databases_unchanged', {}).values()) if payload.get('original_databases_unchanged') else False,
                'client_display_verified': False, 'path': str(path)}
    raise ValueError('不支持的 Windows 桌面操作')


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        request = json.load(sys.stdin)
        result = dispatch(request)
        response = {'ok': True, 'result': result}
    except sqlite3.Error as error:
        empty = 'Name2Id' in str(error)
        response = {'ok': False, 'error': '这个空白账号尚未建立消息身份映射，当前版本不能生成恢复计划。请保留记录，本次未应用。' if empty else '数据库结构不受支持，本次未应用。请保留任务目录。'}
    except UnicodeError:
        response = {'ok': False, 'error': '本机界面与数据的字符编码不兼容，请更新桌面程序并保留任务目录；本次操作未完成。'}
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        detail = str(error)
        if isinstance(error, DirectionMismatch):
            detail = (f'B→A 候选字段2校验失败：实际值={error.actual}，预期值={error.expected}，'
                      f'字段编码={error.wire}，类型={error.local_type}，配置={error.profile}。'
                      '本次未应用；请保留任务，不修改消息字段。')
        elif 'packed direction is not the expected opposite perspective' in detail:
            detail = '消息收发方向与所用方向配置不匹配，本次未应用。请核对密钥旁的同名 .capture.json 回执及旧导出配置；若回执完整，仍需检查消息方向兼容性。请保留任务目录。'
        elif 'Windows 4.2.0.12 currently supports verified text directions only' in detail:
            detail = 'Windows 4.2.0.12 当前仅核验了文本消息方向；所选会话含尚未验证的类型，本次未应用。'
        response = {'ok': False, 'error': detail}
    print(json.dumps(response, ensure_ascii=False))
    return 0 if response['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
