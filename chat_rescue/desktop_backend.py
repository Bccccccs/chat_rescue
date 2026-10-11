"""Private stdin JSON bridge for the native macOS application. No network server."""
import argparse
import json
import os
import re
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import time
import uuid
from . import __main__ as cli
from . import operations as ops
from .sync import make_sync_plan, apply_sync_plan, undo_sync_plan, conversation_count
from .workflow import connect, table_for, ident, SUPPORTED, digest, private_json

BASE = Path.home()/'Library/Application Support/Chat Rescue'
LAUNCHED = []


def running_pid(executable):
    rows = subprocess.run(['/bin/ps','-axo','pid=,comm='],check=True,capture_output=True,text=True).stdout.splitlines()
    for row in rows:
        fields = row.strip().split(None, 1)
        if len(fields) == 2 and Path(fields[1]).resolve() == Path(executable).resolve():
            return int(fields[0])
    return None


def launch_slots(slots):
    launched = {}
    for slot in slots:
        app, executable = cli.app_info(slot_app(slot))
        existing = running_pid(executable)
        if existing:
            launched[slot] = {'state':'already_running','pid':existing}
            continue
        logs = workspace()/'launch-logs'
        logs.mkdir(mode=0o700,exist_ok=True)
        log = logs/(slot+'.log')
        with log.open('ab') as output:
            log.chmod(0o600)
            process = subprocess.Popen([str(executable)],
                stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
        LAUNCHED[:] = [p for p in LAUNCHED if p.poll() is None]
        LAUNCHED.append(process)
        try:
            code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            launched[slot] = {'state':'running','pid':process.pid}
        else:
            raise RuntimeError('账号 '+slot+' 启动后退出（退出码 '+str(code)+'）。日志：'+str(log))
    return {'launched':launched,'instruction':'微信副本进程已运行，请分别核对登录窗口与账号。扫码及手机确认由本人完成。'}


def workspace():
    os.umask(0o077)
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    return BASE


AUTHORIZED_ACTIONS = {'capture','inspect','plan','apply','undo'}


def terminal_receipt(path, response):
    pending=path.with_suffix('.pending')
    private_json(pending,response)
    pending.replace(path)


def authorized_request(request):
    if request.get('action') not in AUTHORIZED_ACTIONS:
        raise ValueError('不支持的终端操作')
    directory = new_job()
    payload = directory/'operation-request.json'
    receipt = directory/'operation-result.json'
    private_json(payload, request)
    command = ([sys.executable] if getattr(sys,'frozen',False) else
               [sys.executable,'-m','chat_rescue.desktop_backend']) + ['--operation-file',str(payload)]
    script = directory/'读取与核验.command'
    script.write_text('#!/bin/zsh\nset -eu\numask 077\ncd '+
                      shlex.quote(str(Path(__file__).resolve().parent.parent))+'\n'+shlex.join(command)+'\n')
    script.chmod(0o700)
    cli.run(['/usr/bin/open','-a','Terminal',str(script)])
    deadline = time.monotonic()+120
    while time.monotonic() < deadline:
        if receipt.exists():
            response = json.loads(receipt.read_text())
            if not response.get('ok'): raise RuntimeError(response.get('error','终端操作未完成'))
            return response['result']
        time.sleep(.2)
    raise RuntimeError('终端操作仍未返回回执，请保留终端窗口和本机任务目录；不要重复应用或撤回。')


def launch_via_terminal(slots):
    # Run from Terminal's existing permission context, including after an app rebuild.
    for slot in slots: cli.app_info(slot_app(slot))
    directory = new_job()
    request = directory/'launch-request.json'
    receipt = directory/'launch-result.json'
    private_json(request, {'slots':slots})
    command = ([sys.executable] if getattr(sys,'frozen',False) else
               [sys.executable,'-m','chat_rescue.desktop_backend']) + ['--launch-file',str(request)]
    script = directory/'打开微信.command'
    script.write_text('#!/bin/zsh\nset -eu\numask 077\ncd '+
                      shlex.quote(str(Path(__file__).resolve().parent.parent))+'\n'+shlex.join(command)+'\n')
    script.chmod(0o700)
    cli.run(['/usr/bin/open','-a','Terminal',str(script)])
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        if receipt.exists():
            result = json.loads(receipt.read_text())
            if not result.get('ok'): raise RuntimeError(result.get('error','微信启动失败'))
            return result['result']
        time.sleep(.2)
    raise RuntimeError('终端启动未完成，请检查终端窗口与系统授权，再重试。')


def slot_app(slot):
    if slot not in ('A','B'):
        raise ValueError('账号槽位无效')
    return workspace()/('WeChat-'+slot+'.app')


def new_job():
    directory = workspace()/'jobs'/uuid.uuid4().hex
    directory.mkdir(parents=True, mode=0o700)
    return directory


def copy_ready(slot):
    app = slot_app(slot)
    marker = app.with_suffix('.prepare.json')
    if not app.is_dir() or not marker.is_file():
        return False
    try:
        import plistlib
        _, executable = cli.app_info(app)
        metadata = json.loads(marker.read_text())
        if metadata.get('signing_revision') != 2:
            return False
        identifier = plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleIdentifier']
        if slot == 'B' and (identifier != 'local.chat-rescue.wechat.B' or metadata.get('bundle_identifier') != identifier):
            return False
        return digest(executable) == metadata.get('copy_executable_sha256')
    except (ValueError, RuntimeError, OSError, KeyError):
        return False


def prepare_slot(original, slot):
    app = slot_app(slot)
    if copy_ready(slot):
        return
    archived = None
    marker = app.with_suffix('.prepare.json')
    if app.exists() or marker.exists():
        archived = workspace()/'copy-revisions'/uuid.uuid4().hex
        archived.mkdir(parents=True, mode=0o700)
        if app.exists(): app.rename(archived/app.name)
        if marker.exists(): marker.rename(archived/marker.name)
    try:
        cli.prepare(original,app,signing_id='com.tencent.xinWeChat' if slot=='A' else 'local.chat-rescue.wechat.B',
                    bundle_identifier='local.chat-rescue.wechat.B' if slot=='B' else None)
    except BaseException:
        if archived:
            if app.exists(): app.rename(archived/('failed-'+app.name))
            if marker.exists(): marker.rename(archived/('failed-'+marker.name))
            if (archived/app.name).exists(): (archived/app.name).rename(app)
            if (archived/marker.name).exists(): (archived/marker.name).rename(marker)
        raise


def owner_from_root(root):
    name = Path(root).parent.name
    return re.sub(r'_[0-9a-fA-F]{4}$', '', name)


def discover_accounts():
    found = {}
    for slot in ('A', 'B'):
        app = slot_app(slot)
        identifier = 'com.tencent.xinWeChat' if slot == 'A' else 'local.chat-rescue.wechat.B'
        if (app/'Contents/Info.plist').is_file():
            import plistlib
            identifier = plistlib.loads((app/'Contents/Info.plist').read_bytes()).get('CFBundleIdentifier', identifier)
        base = Path.home()/'Library/Containers'/identifier/'Data/Documents/xwechat_files'
        candidates = [{'root':str(root), 'owner':owner_from_root(root)} for root in sorted(base.glob('*/db_storage'))
                      if all((root/rel).is_file() for rel in ops.ALL)]
        suggestion = candidates[0] if len(candidates) == 1 else None
        reason = '此微信目录下只有一个完整账号库' if suggestion else '请从候选中核对账号，不能按更新时间猜测'
        pid = running_pid(app/'Contents/MacOS/WeChat')
        if pid:
            rows = subprocess.run(['/usr/sbin/lsof','-Fn','-p',str(pid)],capture_output=True,text=True).stdout.splitlines()
            active = {line[1:].split('/db_storage/')[0]+'/db_storage' for line in rows
                      if line.startswith('n/') and '/db_storage/' in line}
            used = [c for c in candidates if c['root'] in active]
            if len(used) == 1:
                suggestion = used[0]; reason = '正在运行的微信正在使用此账号目录'
            elif len(used) > 1:
                suggestion = None; reason = '微信持有多个账号目录，请本人核对'
        found[slot] = {'candidates':candidates,'suggestion':suggestion,'reason':reason}
    return found


def capture_status(payload):
    payload = Path(payload).resolve(strict=True)
    request = json.loads(payload.read_text())
    receipt = payload.parent/'capture-result.json'
    if not receipt.exists():
        running = payload.parent/'capture-running.json'
        if running.exists():
            pid = int(json.loads(running.read_text())['pid'])
            try: os.kill(pid, 0)
            except ProcessLookupError:
                return {'state':'failed','error':'取钥终端已结束但未完成验证，请重新读取'}
            except PermissionError: pass
        started = request.get('started_at', payload.stat().st_mtime)
        limit = min(max(int(request.get('timeout',300)),10),300) + 45
        if time.time() - started > limit:
            return {'state':'failed','error':'读取等待超时。请检查终端与系统权限后重新读取'}
        return {'state':'waiting'}
    result = json.loads(receipt.read_text())
    if result.get('state') == 'complete':
        output = Path(request['output'])
        if result.get('verified_required_databases') != len(ops.ALL) or not output.is_file():
            return {'state':'failed','error':'取钥回执未完成四库验证，请重试'}
        return {'state':'complete','keys':str(output),'accounts':result.get('accounts',[])}
    return {'state':'failed','error':result.get('error','取钥失败，请查看终端提示')}


def inspect_account(account):
    root = Path(account['root']).resolve(strict=True)
    ops.check_owner(root, account['owner'])
    directory = new_job()/'plain'
    ops.export_root(root, account['keys'], directory)
    contacts = cli.contacts(directory, '')['contacts']
    own = next((r for r in contacts if r['username']==account['owner']), None)
    if not own:
        raise ValueError('数据库联系人中未找到本人身份')
    rows = []
    with connect(directory/'message/message_0.db') as c:
        for contact in contacts:
            peer = contact['username']
            if peer==account['owner'] or '@' in peer: continue
            table=table_for(peer)
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone(): continue
            types={r[0]:r[1] for r in c.execute('SELECT local_type,count(*) FROM '+ident(table)+' GROUP BY local_type')}
            rows.append({**contact,'count':sum(types.values()),'unsupported':sum(n for t,n in types.items() if t not in SUPPORTED)})
    return {'owner':own, 'conversations':rows, 'plain':str(directory)}


def capture_command(request):
    slot=request['slot'];app=slot_app(slot);account=request['account']
    cli.app_info(app)
    selected = account.get('root')
    candidates = [selected] if selected else [c['root'] for c in discover_accounts()[slot]['candidates']]
    if not candidates:
        raise ValueError('尚未找到这个微信的本地记录。请先进入微信，等待记录同步后重试。')
    scope = os.path.commonpath(candidates)
    output=new_job()/'keys.json'
    payload=output.parent/'capture-request.json'
    private_json(payload, {'app':str(app),'db_root':scope,'db_roots':candidates,'output':str(output), 'original_app':request['original'], 'timeout':300,'started_at':time.time()})
    if getattr(sys,'frozen',False):
        command=[sys.executable,'--capture',str(payload)]
    else:
        command=[sys.executable,'-m','chat_rescue.desktop_backend','--capture',str(payload)]
    script=output.parent/'获取密钥.command'
    script.write_text('#!/bin/zsh\nset -euo pipefail\numask 077\n'+
                      'cd '+shlex.quote(str(Path(__file__).resolve().parent.parent))+'\n'+
                      shlex.join(command)+' 2>&1 | /usr/bin/tee '+shlex.quote(str(output.parent/'capture-terminal.log'))+'\nprint "取钥完成，Chat Rescue 会自动继续读取。请回到软件等待下一步。"\n')
    script.chmod(0o700)
    cli.run(['/usr/bin/open','-a','Terminal',str(script)])
    return {'keys':str(output),'request':str(payload),'instruction':'终端已打开。请登录当前账号并完成手机确认，软件会自动检测完成状态并继续读取。两侧依次处理。'}


def dispatch(request):
    action=request['action']
    if action=='authorized-request':return authorized_request(request['request'])
    if action=='launch-via-terminal': return launch_via_terminal(request['slots'])
    if action=='status':
        report=cli.doctor() if not request.get('light') else {}
        report['database_roots']=cli.roots()['database_roots']
        processes=subprocess.run(['/bin/ps','-axo','comm='],check=True,capture_output=True,text=True).stdout.splitlines()
        report['copies']={s:{'path':str(slot_app(s)), 'prepared':copy_ready(s),
                             'running':any(r.strip().startswith(str(slot_app(s))+'/') for r in processes)} for s in ('A','B')}
        report['discovery']=discover_accounts()
        return report
    if action=='prepare-both':
        ops.ensure_closed()
        for slot in ('A','B'):
            prepare_slot(request['original'],slot)
        return {'instruction':'两个副本已准备。首次授予权限后，点击打开两个微信。'}
    if action=='capture-status':return capture_status(request['request'])
    if action=='ensure-closed':
        ops.ensure_closed();return {'state':'closed'}
    if action=='resume':
        directory=Path(request['plan']).resolve(strict=True)
        manifest=json.loads((directory/'sync-plan.json').read_text())
        if manifest.get('version')!=1 or set(manifest['accounts'])!={'A','B'}:
            raise ValueError('此目录不是受支持的双向补齐计划')
        receipt=directory/'sync-result.json'
        state=json.loads(receipt.read_text())['state'] if receipt.exists() else 'ready'
        if (directory/'sync-started.json').exists() and not receipt.exists(): state='interrupted'
        if (directory/'undo-result.json').exists(): state='undone'
        return {'plan':str(directory),'directions':manifest['directions'],'accounts':manifest['accounts'],'state':state}
    if action=='prepare':
        slot=request['slot']
        return cli.prepare(request['original'],slot_app(slot),signing_id='com.tencent.xinWeChat' if slot=='A' else 'local.chat-rescue.wechat.B',
                           bundle_identifier='local.chat-rescue.wechat.B' if slot=='B' else None)
    if action=='launch':
        return launch_slots(request['slots'])
    if action=='capture':return capture_command(request)
    if action=='inspect':return inspect_account(request['account'])
    if action=='plan':return make_sync_plan(request['a'],request['b'],new_job()/'sync')
    if action=='apply':return apply_sync_plan(request['plan'])
    if action=='undo':return undo_sync_plan(request['plan'])
    raise ValueError('不支持的桌面操作')


def main():
    os.umask(0o077)
    if getattr(sys,'frozen',False):
        bundled=Path(sys._MEIPASS)/'libsqlcipher.dylib'
        if bundled.exists(): os.environ['SQLCIPHER_LIBRARY']=str(bundled)
    p=argparse.ArgumentParser();p.add_argument('--capture');p.add_argument('--launch-file');p.add_argument('--operation-file');args=p.parse_args()
    if args.operation_file:
        payload=Path(args.operation_file).resolve(strict=True)
        try:
            request=json.loads(payload.read_text())
            if request.get('action') not in AUTHORIZED_ACTIONS: raise ValueError('不支持的终端操作')
            response={'ok':True,'result':dispatch(request)}
        except (ValueError,RuntimeError,OSError,KeyError,sqlite3.Error) as e:
            response={'ok':False,'error':str(e)}
        terminal_receipt(payload.parent/'operation-result.json',response)
        # The private receipt may contain account metadata; do not print it in Terminal.
        print('本机步骤完成，返回 Chat Rescue 查看结果。' if response['ok'] else '本机步骤未完成，返回 Chat Rescue 查看错误详情。')
        return 0 if response['ok'] else 2
    if args.launch_file:
        request=Path(args.launch_file).resolve(strict=True)
        try:
            result=launch_slots(json.loads(request.read_text())['slots'])
            response={'ok':True,'result':result}
        except (ValueError,RuntimeError,OSError,KeyError) as e:
            response={'ok':False,'error':str(e)}
        private_json(request.parent/'launch-result.json',response)
        print(json.dumps(response,ensure_ascii=False))
        return 0 if response['ok'] else 2
    try:
        if args.capture:
            private_json(Path(args.capture).parent/'capture-running.json', {'pid':os.getpid()})
            try:
                result=cli.capture(argparse.Namespace(**json.loads(Path(args.capture).read_text())))
            except Exception:
                private_json(Path(args.capture).parent/'capture-result.json', {'state':'failed','error':'取钥未完成，请检查终端中的权限、退出状态和登录提示'})
                raise
            private_json(Path(args.capture).parent/'capture-result.json', {'state':'complete','verified_required_databases':result['verified_required_databases'],'accounts':result['accounts']})
        else:
            result=dispatch(json.load(sys.stdin))
        print(json.dumps({'ok':True,'result':result},ensure_ascii=False));return 0
    except sqlite3.Error:
        print(json.dumps({'ok':False,'error':'数据库结构不受支持，请保留本机报告。'},ensure_ascii=False));return 2
    except (ValueError,RuntimeError,OSError,KeyError) as e:
        print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
