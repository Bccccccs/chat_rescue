"""Public CLI. Runtime data belongs in a private directory outside this repo."""
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sqlite3
import sys
from . import __version__
from .workflow import save_chat, connect, digest, private_json
from .operations import ALL, mac_only, ensure_closed, check_owner, export_root, make_plan, apply_plan
from .vendor.sqlcipher_probe import library, load_key, DB


def run(args):
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError('系统命令失败：' + Path(args[0]).name)
    return p.stdout or p.stderr


def app_info(path):
    path = Path(path).resolve(strict=True)
    if path.suffix != '.app':
        raise ValueError('请选择微信 .app')
    with (path / 'Contents/Info.plist').open('rb') as f:
        info = plistlib.load(f)
    identifier = info.get('CFBundleIdentifier')
    trusted_copy = False
    if identifier in ('local.chat-rescue.wechat.A', 'local.chat-rescue.wechat.B'):
        marker = path.with_suffix('.prepare.json')
        if marker.is_file():
            prepared = json.loads(marker.read_text())
            trusted_copy = prepared.get('bundle_identifier') == identifier
    if (identifier != 'com.tencent.xinWeChat' and not trusted_copy) or info.get('CFBundleShortVersionString') != '4.1.13':
        raise ValueError('首版只接受已实测的微信 4.1.13；其他版本需重新适配验证')
    exe = path / 'Contents/MacOS' / info['CFBundleExecutable']
    if not exe.is_file():
        raise ValueError('微信可执行文件不存在')
    return path, exe


def prepare(app, output, signing_id=None, bundle_identifier=None):
    mac_only(); ensure_closed()
    if os.geteuid() == 0:
        raise RuntimeError('请勿使用 sudo/root')
    app, exe = app_info(app)
    if bundle_identifier and bundle_identifier not in ('local.chat-rescue.wechat.A', 'local.chat-rescue.wechat.B'):
        raise ValueError('副本应用标识不受支持')
    output = Path(output).resolve()
    marker = output.with_suffix('.prepare.json')
    if output.exists() or marker.exists() or output.is_relative_to(app) or app.is_relative_to(output):
        raise ValueError('副本路径必须为独立的新路径')
    os.umask(0o077); output.parent.mkdir(parents=True, exist_ok=True)
    signature = run(['/usr/bin/codesign', '-dvv', str(app)])
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
    run(['/usr/bin/ditto', str(app), str(output)])
    if bundle_identifier:
        info_path = output / 'Contents/Info.plist'
        info = plistlib.loads(info_path.read_bytes())
        info['CFBundleIdentifier'] = bundle_identifier
        info['CFBundleDisplayName'] = '微信 ' + bundle_identifier.rsplit('.', 1)[1]
        info_path.write_bytes(plistlib.dumps(info))
    # Derive nested identifiers from their own bundles. Passing --identifier with
    # --deep overwrites helper identities too and breaks the debug startup path.
    run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(output)])
    sign_args = ['/usr/bin/codesign', '--force', '--sign', '-']
    if signing_id:
        sign_args += ['--identifier', signing_id]
    run([*sign_args, str(output)])
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(output)])
    if run(['/usr/bin/codesign', '-dvv', str(app)]) != signature:
        raise RuntimeError('原版签名发生变化，请先检查原版')
    private_json(marker, {'original': str(app), 'signature': signature, 'signing_identifier': signing_id, 'bundle_identifier': bundle_identifier,
                         'signing_revision': 2,
                         'original_executable_sha256': digest(exe),
                         'copy_executable_sha256': digest(output / 'Contents/MacOS' / exe.name)})
    return {'copy': str(output), 'instruction': '为此固定副本授权完全磁盘访问；在原生 Terminal 执行 capture-keys。'}


def capture(args):
    mac_only(); ensure_closed()
    app, exe = app_info(args.app)
    marker = json.loads(app.with_suffix('.prepare.json').read_text())
    original, original_exe = app_info(args.original_app or marker['original'])
    if str(original) != marker['original'] or original == app or digest(exe) != marker['copy_executable_sha256']:
        raise ValueError('请使用 prepare-key-app 创建的固定副本及对应原版')
    if digest(original_exe) != marker['original_executable_sha256']:
        raise ValueError('原版已更新；重新创建副本')
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError('密钥输出已存在；选择新文件名')
    output.parent.mkdir(parents=True, exist_ok=True)
    root = Path(args.db_root).resolve(strict=True)
    database_roots = [Path(p).resolve(strict=True) for p in getattr(args, 'db_roots', None) or [root]]
    for candidate in database_roots:
        if not candidate.is_relative_to(root):
            raise ValueError('账号候选必须位于本次选定的微信目录内')
        for rel in ALL:
            if not (candidate / rel).is_file():
                raise ValueError('请先登录微信并等待本地记录建立：' + rel)
    groups = output.parent / 'capture-groups.json'
    private_json(groups, [[str((candidate/rel).relative_to(root)) for rel in ALL] for candidate in database_roots])
    env = dict(os.environ, PYTHONPATH=run(['/usr/bin/lldb', '-P']).strip())
    print('副本启动后登录本次账号并完成手机确认；验证结束会关闭本次副本。密钥不会输出到终端。', flush=True)
    try:
        result = subprocess.run(['/usr/bin/python3', str(Path(__file__).parent / 'vendor/capture_keys.py'),
                                 '--exe', str(exe), '--db-root', str(root), '--out', str(output),
                                 '--timeout', str(args.timeout), '--completion-groups', str(groups)], env=env)
    finally:
        # Keep the fixed path for TCC permissions, but prevent LaunchServices
        # from preferring the duplicate bundle when the user opens the original.
        ls = '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'
        subprocess.run([ls, '-u', str(app)], capture_output=True)
        if run(['/usr/bin/codesign', '-dvv', str(original)]) != marker['signature'] or digest(original_exe) != marker['original_executable_sha256']:
            raise RuntimeError('原版签名或可执行文件改变，请检查后再启动')
    if result.returncode:
        raise RuntimeError('取钥未完成，请按故障处理文档检查权限和副本登录状态')
    verified_roots = []
    for candidate in database_roots:
        try:
            for rel in ALL:
                with DB(candidate / rel, load_key(output, candidate / rel)) as d:
                    d.query('SELECT count(*) FROM sqlite_master')
            import re
            from .cipher import literal
            owner = re.sub(r'_[0-9a-fA-F]{4}$', '', candidate.parent.name)
            with DB(candidate/'contact/contact.db', load_key(output, candidate/'contact/contact.db')) as d:
                rows = d.query('SELECT username,nick_name FROM contact WHERE username=' + literal(owner))
            if len(rows) == 1:
                verified_roots.append({'root':str(candidate),'owner':owner,'nickname':rows[0]['nick_name'] or '昵称未设置'})
        except (RuntimeError, ValueError, KeyError):
            continue
    if not verified_roots:
        raise RuntimeError('尚未完成任何账号的四库及本人身份验证，请检查登录与权限')
    return {'keys': str(output), 'verified_required_databases': len(ALL),
            'accounts':verified_roots,
            'instruction': '四个必要数据库验证通过；副本已关闭。导出前保持微信退出。'}


def contacts(root, query):
    with connect(Path(root) / 'contact/contact.db') as c:
        columns = {r[1] for r in c.execute('PRAGMA table_info(contact)')}
        fields = [x for x in ('username', 'nick_name', 'remark') if x in columns]
        if 'username' not in fields:
            raise ValueError('联系人表结构不受支持')
        rows = [dict(r) for r in c.execute('SELECT ' + ','.join(fields) + ' FROM contact')]
    return {'contacts': [r for r in rows if query.casefold() in ' '.join(str(v or '') for v in r.values()).casefold()]}


def roots():
    identifiers = ('com.tencent.xinWeChat', 'local.chat-rescue.wechat.A', 'local.chat-rescue.wechat.B')
    found = []
    for identifier in identifiers:
        base = Path.home() / 'Library/Containers' / identifier / 'Data/Documents/xwechat_files'
        found.extend(str(p) for p in sorted(base.glob('*/db_storage')))
    return {'database_roots': found,
            'instruction': '从刚登录账号核对目录，不能自动根据修改时间选择账号。'}


def doctor():
    checks = {'macOS_Apple_Silicon': platform.system() == 'Darwin' and platform.machine() == 'arm64',
              'python_3_14': sys.version_info >= (3, 14)}
    try:
        library(); checks['sqlcipher'] = True
    except (RuntimeError, OSError):
        checks['sqlcipher'] = False
    try:
        run(['/usr/bin/lldb', '-P']); checks['apple_lldb'] = True
    except (RuntimeError, OSError):
        checks['apple_lldb'] = False
    return {'version': __version__, 'checks': checks, 'ready': all(checks.values()),
            'supported_wechat': '4.1.13', 'instruction': '失败项见 README；系统权限需首次取钥时验证。'}


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description='微信双方授权单聊恢复：Mac experimental')
    p.add_argument('--version', action='version', version=__version__)
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor'); commands.add_parser('roots')
    c = commands.add_parser('prepare-key-app'); c.add_argument('--app', required=True); c.add_argument('--output', required=True)
    c = commands.add_parser('capture-keys'); c.add_argument('--app', required=True); c.add_argument('--db-root', required=True); c.add_argument('--output', required=True); c.add_argument('--original-app'); c.add_argument('--timeout', type=int, default=180)
    c = commands.add_parser('export-db'); c.add_argument('--db-root', required=True); c.add_argument('--keys', required=True); c.add_argument('--output', required=True)
    c = commands.add_parser('contacts'); c.add_argument('--plain-root', required=True); c.add_argument('--query', required=True)
    c = commands.add_parser('save-chat'); c.add_argument('--plain-root', required=True); c.add_argument('--owner', required=True); c.add_argument('--peer', required=True); c.add_argument('--output', required=True)
    c = commands.add_parser('plan-restore'); c.add_argument('--source', required=True); c.add_argument('--target-db-root', required=True); c.add_argument('--target-keys', required=True); c.add_argument('--target-owner', required=True); c.add_argument('--output', required=True)
    c = commands.add_parser('apply'); c.add_argument('--plan', required=True)
    a = p.parse_args()
    try:
        if a.command == 'doctor':
            result = doctor()
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result['ready'] else 2
        if a.command == 'roots': result = roots()
        elif a.command == 'prepare-key-app': result = prepare(a.app, a.output)
        elif a.command == 'capture-keys': result = capture(a)
        elif a.command == 'export-db': result = export_root(a.db_root, a.keys, a.output)
        elif a.command == 'contacts': result = contacts(a.plain_root, a.query)
        elif a.command == 'save-chat':
            r = json.loads((Path(a.plain_root) / 'export-report.json').read_text())
            account = r['account_directory']
            if account != a.owner and not account.startswith(a.owner + '_'):
                raise ValueError('owner 与导出账号目录不匹配')
            if any(digest(Path(a.plain_root) / rel) != h for rel, h in r['exported'].items()):
                raise ValueError('导出数据库哈希改变')
            result = save_chat(a.plain_root, a.owner, a.peer, a.output)
        elif a.command == 'plan-restore': result = make_plan(a.source, a.target_db_root, a.target_keys, a.target_owner, a.output)
        else: result = apply_plan(a.plan)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except sqlite3.Error:
        print(json.dumps({'state': 'failed', 'error': '数据库结构或 SQL 不受支持；请查看故障处理文档'}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (ValueError, RuntimeError, OSError, KeyError) as e:
        # No traceback or raw SQL/content is emitted. Keep this output private too.
        print(json.dumps({'state': 'failed', 'error': str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
