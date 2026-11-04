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
from .workflow import connect, digest, private_json
from .sync import make_sync_plan, apply_sync_plan, undo_sync_plan
from .windows_capture import app_info, capture
from .vendor.sqlcipher_probe import DB, load_key
from .cipher import literal
from .shards import database_rels, receipt_covers


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


def discover():
    bases = set()
    config = Path(os.environ.get('APPDATA', Path.home())) / 'Tencent/xwechat/config'
    for path in config.glob('*.ini'):
        if path.stat().st_size > 4096:
            continue
        try:
            value = path.read_text(encoding='utf-8-sig').strip().rstrip('\0')
            if value and Path(value).is_absolute():
                base = Path(value)
                bases.add(base if base.name == 'xwechat_files' else base / 'xwechat_files')
        except (OSError, UnicodeError, ValueError):
            continue
    found = set(cli.roots()['database_roots'])
    for base in bases:
        found.update(str(p.resolve()) for p in base.glob('*/db_storage'))
    labels = {}
    # A fresh deployment must not borrow identities from unrelated experiments.
    # Only this workspace's explicitly saved account settings supply cached labels.
    for item in saved_settings().get('accounts', []):
        if item.get('root') and item.get('nickname'):
            labels[str(Path(item['root']).resolve())] = item['nickname'] + ' · 上次读取'
    return [{'root': str(Path(root).resolve()),
             'owner': re.sub(r'_[0-9a-fA-F]{4}$', '', Path(root).parent.name),
             'label': labels.get(str(Path(root).resolve()), '账号目录 ' + str(i + 1) + ' · 读取后识别昵称'),
             'complete': all((Path(root) / rel).is_file() for rel in ALL)}
            for i, root in enumerate(sorted(found))]


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
        rels = database_rels(root)
        files = (W.LPCWSTR * len(rels))(*(str(Path(root) / rel) for rel in rels))
        if dll.RmRegisterResources(session, len(rels), files, 0, None, 0, None):
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
    root = Path(result['root']).resolve(strict=True)
    if not result['owner']:
        raise ValueError('请先选择并核对账号')
    check_owner(root, result['owner'])
    if not all((root / rel).is_file() for rel in database_rels(root)):
        raise ValueError('所选账号缺少四个必要数据库')
    result['root'] = str(root)
    return result


def account_nickname(selected, keys):
    contact = Path(selected['root']) / 'contact/contact.db'
    with DB(contact, load_key(keys, contact)) as database:
        rows = database.query('SELECT username,nick_name FROM contact WHERE username=' + literal(selected['owner']))
    if len(rows) != 1:
        raise ValueError('四库密钥已保存，但未能唯一核对本人联系人身份；请保留任务回执')
    return rows[0]['nick_name'] or '未设置昵称'


def plan_view(directory):
    directory = Path(directory).resolve(strict=True)
    manifest = json.loads((directory / 'sync-plan.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or set(manifest.get('accounts', {})) != {'A', 'B'}:
        raise ValueError('这不是受支持的双向计划')
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
            'accounts': manifest['accounts'], 'directions': manifest['directions'], 'state': state}


def guarded_plan(request):
    view = plan_view(request['plan'])
    if not request.get('plan_hash') or view['plan_hash'] != request['plan_hash']:
        raise ValueError('计划已变化，请重新载入并核对预览')
    return view


def dispatch(request):
    action = request.get('action')
    if action == 'status':
        return {'doctor': cli.doctor(), 'executable': find_executable(), 'candidates': discover(),
                'workspace': str(workspace()), 'platform': 'Windows', 'settings': saved_settings()}
    if action == 'save-settings':
        return save_settings(request)
    if action == 'prepare':
        return {'doctor': cli.doctor(), 'app': app_info(request['executable']), 'workspace': str(workspace())}
    if action == 'launch':
        slot = request.get('slot')
        if slot not in ('A', 'B'):
            raise ValueError('请指定先打开 A，核验并正常退出后再打开 B')
        metadata = app_info(request['executable'])
        ensure_closed()
        if slot == 'B':
            first = account(request.get('a', {}))
            if not first['keys']:
                raise ValueError('请先读取并核验 A，再打开 B')
            require_private(first['keys'])
            cli.verify_keys(first['root'], first['keys'])
            account_nickname(first, first['keys'])
        process = subprocess.Popen([metadata['executable']], cwd=str(Path(metadata['executable']).parent),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'slot': slot, 'pids': [process.pid],
                'instruction': ('当前处理账号 B。登录保留记录的账号后，现在从手机 B 将与 A 的聊天导入电脑微信 B；'
                                '已完整保存在电脑 B 时可直接核对。确认内容与时间范围完整后，勾选确认并读取账号 B。')
                               if slot == 'B' else '当前处理账号 A。请本人登录并核对目标账号，再点击“读取账号 A”。'}
    if action == 'capture':
        if request.get('slot') == 'B' and request.get('source_history_confirmed') is not True:
            raise ValueError('请先完成导入或确认电脑 B 已有完整记录，并核对与 A 的聊天后再读取 B')
        selected = account(request['account'])
        if request.get('identity_confirmed') is not True:
            raise ValueError('请确认本次账号归属后再读取')
        main = set(main_processes(request['executable']))
        if not main and selected.get('keys'):
            ensure_closed()
            require_private(selected['keys'])
            cli.verify_keys(selected['root'], selected['keys'])
            return {'state': 'verified', 'account': {**selected,
                    'nickname': account_nickname(selected, selected['keys'])}}
        pids = resource_processes(selected['root'])
        bound = [pid for pid in pids if pid in main]
        if len(bound) != 1:
            raise ValueError('无法唯一对应所选账号与微信进程，请核对本次登录与目录')
        directory = job()
        result = capture(bound[0], request['executable'], selected['root'], selected['owner'], directory / 'keys.json')
        return {**result, 'account': {**selected, 'keys': result['keys'],
                'nickname': account_nickname(selected, result['keys'])}}
    if action == 'plan':
        ensure_closed()
        if request.get('identity_confirmed') is not True:
            raise ValueError('请先核对双方账号身份')
        if request.get('source_history_confirmed') is not True:
            raise ValueError('请先确认手机 B 的所需聊天已导入或完整保存在电脑 B，并重新读取核验')
        a, b = account(request['a']), account(request['b'])
        for selected in (a, b):
            require_private(selected['keys'])
        directory = job() / 'sync'
        make_sync_plan(a, b, directory)
        return plan_view(directory)
    if action == 'resume':
        return plan_view(request['plan'])
    if action == 'apply':
        view = guarded_plan(request)
        if request.get('confirmed') is not True:
            raise ValueError('请确认这份具体预览后再应用')
        if view['state'] != 'ready':
            raise ValueError('此任务已有回执或中断标记，不能再次应用')
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
        if 'packed direction is not the expected opposite perspective' in detail:
            detail = '消息收发方向与已核验版本不匹配，本次未应用。请保留任务目录，核对 Windows 取钥回执与完整版本。'
        elif 'Windows 4.2.0.12 currently supports verified text directions only' in detail:
            detail = 'Windows 4.2.0.12 当前仅核验了文本消息方向；所选会话含尚未验证的类型，本次未应用。'
        response = {'ok': False, 'error': detail}
    print(json.dumps(response, ensure_ascii=False))
    return 0 if response['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
