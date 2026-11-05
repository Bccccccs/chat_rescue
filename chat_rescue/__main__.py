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
from .operations import files_for, inspection_files, ALL, mac_only, ensure_closed, check_owner, export_root, make_plan, apply_plan
from .vendor.sqlcipher_probe import library, load_key, DB
from .runtime import supported_host
from .shards import database_rels, receipt_covers


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
    marker = json.loads(app.with_suffix('.prepare.json').read_text(encoding='utf-8'))
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
    include_voice = getattr(args, 'include_voice', False)
    if include_voice:
        from .voice import single_media
    database_roots = [Path(p).resolve(strict=True) for p in getattr(args, 'db_roots', None) or [root]]
    required_by_root = {}
    for candidate in database_roots:
        if not candidate.is_relative_to(root):
            raise ValueError('账号候选必须位于本次选定的微信目录内')
        if include_voice:
            single_media(candidate)
        required = inspection_files(candidate, include_voice)
        required_by_root[candidate] = required
        for rel in required:
            if not (candidate / rel).is_file():
                raise ValueError('请先登录微信并等待本地记录建立：' + rel)
    groups = output.parent / 'capture-groups.json'
    private_json(groups, [[str((candidate/rel).relative_to(root)) for rel in required_by_root[candidate]] for candidate in database_roots])
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
    verified_databases = {}
    for candidate in database_roots:
        try:
            required = required_by_root[candidate]
            if inspection_files(candidate, include_voice) != required:
                raise ValueError('取钥期间消息分片清单改变，请重新读取')
            for rel in required:
                with DB(candidate / rel, load_key(output, candidate / rel)) as d:
                    if d.query('PRAGMA integrity_check;') != [{'integrity_check': 'ok'}]:
                        raise ValueError('必要数据库完整性失败：' + rel)
            import re
            from .cipher import literal
            owner = re.sub(r'_[0-9a-fA-F]{4}$', '', candidate.parent.name)
            with DB(candidate/'contact/contact.db', load_key(output, candidate/'contact/contact.db')) as d:
                rows = d.query('SELECT username,nick_name FROM contact WHERE username=' + literal(owner))
            if len(rows) == 1:
                if inspection_files(candidate, include_voice) != required:
                    raise ValueError('验证期间消息分片清单改变，请重新读取')
                verified_roots.append({'root':str(candidate),'owner':owner,'nickname':rows[0]['nick_name'] or '昵称未设置'})
                verified_databases[str(candidate)] = list(required)
        except (RuntimeError, ValueError, KeyError):
            continue
    if not verified_roots:
        raise RuntimeError('尚未完成任何账号的必要数据库及本人身份验证，请检查登录与权限')
    return {'keys': str(output), 'verified_required_databases': max(map(len, verified_databases.values())),
            'verified_databases': verified_databases,
            'accounts':verified_roots,
            'instruction': '必要数据库验证通过；副本已关闭。导出前保持微信退出。'}


def contacts(root, query):
    with connect(Path(root) / 'contact/contact.db') as c:
        columns = {r[1] for r in c.execute('PRAGMA table_info(contact)')}
        fields = [x for x in ('username', 'nick_name', 'remark') if x in columns]
        if 'username' not in fields:
            raise ValueError('联系人表结构不受支持')
        rows = [dict(r) for r in c.execute('SELECT ' + ','.join(fields) + ' FROM contact')]
    return {'contacts': [r for r in rows if query.casefold() in ' '.join(str(v or '') for v in r.values()).casefold()]}


def roots(search_paths=None):
    if platform.system() == 'Windows':
        import ctypes
        # CSIDL_PERSONAL follows redirected Documents (including OneDrive).
        documents = ctypes.create_unicode_buffer(32768)
        rc = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, documents)
        bases = [Path(documents.value) / 'xwechat_files'] if rc == 0 else []
        bases += [Path.home() / 'Documents/xwechat_files']
        bases += [Path(p).expanduser().resolve(strict=True) for p in search_paths or []]
        found = set()
        for base in bases:
            candidates = [base] if base.name == 'db_storage' else base.glob('*/db_storage')
            found.update(str(p.resolve()) for p in candidates if p.is_dir())
        return {'database_roots': sorted(found),
                'instruction': '请本人核对账号；自定义位置用 roots --search-path 指定 xwechat_files 或 db_storage，不能按修改时间选账号。'}
    identifiers = ('com.tencent.xinWeChat', 'local.chat-rescue.wechat.A', 'local.chat-rescue.wechat.B')
    found = []
    for identifier in identifiers:
        base = Path.home() / 'Library/Containers' / identifier / 'Data/Documents/xwechat_files'
        found.extend(str(p) for p in sorted(base.glob('*/db_storage')))
    for base in search_paths or []:
        base = Path(base).expanduser().resolve(strict=True)
        found.extend(str(p) for p in ([base] if base.name == 'db_storage' else sorted(base.glob('*/db_storage'))) if p.is_dir())
    return {'database_roots': found,
            'instruction': '从刚登录账号核对目录，不能自动根据修改时间选择账号。'}


def doctor():
    windows = platform.system() == 'Windows'
    try:
        supported_host(); host = True
    except RuntimeError:
        host = False
    checks = {'supported_host': host, 'python_3_14': sys.version_info >= (3, 14)}
    try:
        library(); checks['sqlcipher'] = True
    except (RuntimeError, OSError):
        checks['sqlcipher'] = False
    if not windows:
        try:
            run(['/usr/bin/lldb', '-P']); checks['apple_lldb'] = True
        except (RuntimeError, OSError):
            checks['apple_lldb'] = False
    crypto_ready = False
    if host and checks['sqlcipher']:
        try:
            from .attachments import aes
            sample = b'chat-rescue-private-self-test'
            key = b'0123456789abcdef'
            crypto_ready = aes(aes(sample,key,True),key,False) == sample
        except (ValueError,RuntimeError,OSError):
            pass
    return {'version': __version__, 'attachment_crypto_ready': crypto_ready, 'checks': checks, 'ready': all(checks.values()),
            'supported_wechat': 'Windows: 未完成客户端实测，仅接受兼容四库结构与 SQLCipher 4' if windows else '4.1.13',
            'key_capture_available': not windows and host,
            'experimental_windows_capture_available': windows and host,
            'windows_capture_target': '4.2.x.x x64, pending client validation' if windows else None,
            'instruction': 'Windows ready 仅指离线依赖就绪；需要本人授权的已有密钥文件，先运行 verify-keys。' if windows else '失败项见 README；系统权限需首次取钥时验证。'}


def verify_keys(root, keys, *, include_voice=False):
    supported_host(); ensure_closed()
    root = Path(root).resolve(strict=True)
    if include_voice:
        from .voice import single_media
        single_media(root)
    required = inspection_files(root, include_voice)
    from .operations import state
    before = state(root, include_voice)
    for rel in required:
        with DB(root / rel, load_key(keys, root / rel)) as database:
            if database.query('PRAGMA integrity_check;') != [{'integrity_check': 'ok'}]:
                raise ValueError('必要数据库完整性验证失败：' + rel)
    after = state(root, include_voice)
    if (inspection_files(root, include_voice) != required or set(after) != set(before)
            or any(after[rel] != value for rel,value in before.items() if not rel.endswith('-shm'))):
        raise ValueError('验证期间消息分片或数据库改变，请退出微信后重新读取')
    return {'verified_required_databases': len(required), 'verified_databases': list(required),
            'instruction': '必要数据库密钥与完整性验证通过；尚需核对双方身份与数据库结构。'}


def main():
    if os.name == 'nt':
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8')
    os.umask(0o077)
    p = argparse.ArgumentParser(description='微信双方授权单聊恢复：Mac / Windows offline experimental')
    p.add_argument('--version', action='version', version=__version__)
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor')
    c = commands.add_parser('init-work'); c.add_argument('--output', required=True)
    c = commands.add_parser('roots'); c.add_argument('--search-path', action='append')
    c = commands.add_parser('verify-keys'); c.add_argument('--db-root', required=True); c.add_argument('--keys', required=True)
    c = commands.add_parser('inspect-windows-app'); c.add_argument('--exe', required=True)
    c = commands.add_parser('capture-windows-keys')
    c.add_argument('--pid', type=int, required=True); c.add_argument('--exe', required=True)
    c.add_argument('--db-root', required=True); c.add_argument('--owner', required=True)
    c.add_argument('--output', required=True); c.add_argument('--timeout', type=int, default=120)
    for name in ('plan-sync','plan-replace'):
        c = commands.add_parser(name)
        for slot in ('a', 'b'):
            for field in ('root', 'keys', 'owner'):
                c.add_argument('--' + slot + '-' + field, required=True)
        c.add_argument('--output', required=True)
    c = commands.add_parser('apply-sync'); c.add_argument('--plan', required=True)
    c = commands.add_parser('undo-sync'); c.add_argument('--plan', required=True)
    c = commands.add_parser('prepare-key-app'); c.add_argument('--app', required=True); c.add_argument('--output', required=True)
    c = commands.add_parser('capture-keys'); c.add_argument('--app', required=True); c.add_argument('--db-root', required=True); c.add_argument('--output', required=True); c.add_argument('--original-app'); c.add_argument('--timeout', type=int, default=180)
    c = commands.add_parser('export-db'); c.add_argument('--db-root', required=True); c.add_argument('--keys', required=True); c.add_argument('--output', required=True)
    c = commands.add_parser('contacts'); c.add_argument('--plain-root', required=True); c.add_argument('--query', required=True)
    c = commands.add_parser('save-chat'); c.add_argument('--plain-root', required=True); c.add_argument('--owner', required=True); c.add_argument('--peer', required=True); c.add_argument('--output', required=True)
    c = commands.add_parser('plan-restore'); c.add_argument('--source', required=True); c.add_argument('--target-db-root', required=True); c.add_argument('--target-keys', required=True); c.add_argument('--target-owner', required=True); c.add_argument('--output', required=True)
    c = commands.add_parser('apply'); c.add_argument('--plan', required=True)
    for name in ('verify-keys','capture-keys','capture-windows-keys','export-db','save-chat','plan-restore','plan-sync','plan-replace'):
        commands.choices[name].add_argument('--include-voice', action='store_true', help='启用语音离线实验模式；需媒体库密钥，客户端播放及手机迁移待验收')
    for name in ('export-db','save-chat','plan-restore','plan-sync','plan-replace'):
        commands.choices[name].add_argument('--include-attachments', action='store_true', help='启用图片、视频和文件附件实验模式；需完整本地文件，客户端打开仍待验收')
    a = p.parse_args()
    voice_options = {'include_voice': True} if getattr(a, 'include_voice', False) else {}
    if getattr(a, 'include_attachments', False):voice_options['include_attachments']=True
    try:
        if a.command == 'doctor':
            result = doctor()
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result['ready'] else 2
        if a.command == 'roots': result = roots(a.search_path)
        elif a.command == 'init-work':
            from .operations import fresh
            result = {'workspace': str(fresh(a.output))}
        elif a.command == 'verify-keys': result = verify_keys(a.db_root, a.keys, **voice_options)
        elif a.command == 'inspect-windows-app':
            from .windows_capture import app_info as windows_app_info
            result = windows_app_info(a.exe)
        elif a.command == 'capture-windows-keys':
            from .windows_capture import capture as windows_capture
            result = windows_capture(a.pid, a.exe, a.db_root, a.owner, a.output, a.timeout, **voice_options)
        elif a.command == 'plan-sync':
            from .sync import make_sync_plan
            result = make_sync_plan(*({field: getattr(a, slot + '_' + field) for field in ('root', 'keys', 'owner')} for slot in ('a', 'b')), a.output, **voice_options)
        elif a.command == 'plan-replace':
            from .replacement import make_replace_plan
            result = make_replace_plan(*({field: getattr(a, slot + '_' + field) for field in ('root', 'keys', 'owner')} for slot in ('a', 'b')), a.output, **voice_options)
        elif a.command in ('apply-sync', 'undo-sync'):
            from .sync import apply_sync_plan, undo_sync_plan
            result = (apply_sync_plan if a.command == 'apply-sync' else undo_sync_plan)(a.plan)
        elif a.command == 'prepare-key-app': result = prepare(a.app, a.output)
        elif a.command == 'capture-keys': result = capture(a)
        elif a.command == 'export-db': result = export_root(a.db_root, a.keys, a.output, **voice_options)
        elif a.command == 'contacts': result = contacts(a.plain_root, a.query)
        elif a.command == 'save-chat':
            if not (Path(a.plain_root) / 'export-report.json').is_file():
                raise ValueError('缺少 export-report.json，请使用完整的项目导出')
            result = save_chat(a.plain_root, a.owner, a.peer, a.output, **voice_options)
        elif a.command == 'plan-restore': result = make_plan(a.source, a.target_db_root, a.target_keys, a.target_owner, a.output, **voice_options)
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
