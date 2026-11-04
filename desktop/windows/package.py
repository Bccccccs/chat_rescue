"""Build a per-user Windows installer from pinned, verified runtime archives.

Does not inspect, copy, launch or change WeChat. Only explicit public source
files and verified dependency archives enter the payload.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PYTHON_URL = 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip'
PYTHON_SHA256 = '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'
CIPHER_URL = 'https://api.nuget.org/v3-flatcontainer/sqlitepclraw.lib.e_sqlcipher/2.1.11/sqlitepclraw.lib.e_sqlcipher.2.1.11.nupkg'
CIPHER_SHA256 = '461ef5dd142784b44b22662f853dd5de7a2d28e9c98e0165f26733d81a355696'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def archive(url, expected, cache, provided=None):
    path = Path(provided).resolve(strict=True) if provided else cache / url.rsplit('/', 1)[1]
    if not path.exists():
        partial = path.with_suffix(path.suffix + '.partial')
        with urllib.request.urlopen(url, timeout=120) as response, partial.open('xb') as destination:
            shutil.copyfileobj(response, destination)
        if sha256(partial) != expected:
            raise ValueError('Dependency download hash mismatch: ' + path.name)
        partial.replace(path)
    if sha256(path) != expected:
        raise ValueError('Dependency archive hash mismatch: ' + path.name)
    return path


def write_config(payload):
    # Installed paths are resolved by the shell against its own directory.
    config = {'python': r'runtime\python.exe', 'sqlcipher': r'runtime\e_sqlcipher.dll',
              'repository': 'app', 'executable': ''}
    (payload / 'backend-config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')


def copy_source(payload):
    names = subprocess.check_output(['git', 'ls-files', '-z', '--', 'chat_rescue', 'docs',
                                    'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'CHANGELOG.md',
                                    'desktop/windows/licenses'], cwd=ROOT).decode('utf-8').split('\0')
    for name in filter(None, names):
        relative = Path(name)
        source = ROOT / relative
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError('Release source must stay within the checkout')
        if name.startswith('chat_rescue/') and (source.suffix == '.py' or source.name == 'LICENSE'):
            target = payload / 'app' / relative
        elif name.startswith('desktop/windows/licenses/') and source.suffix == '.txt':
            target = payload / 'licenses' / source.name
        elif name.startswith('docs/') and source.suffix in ('.md', '.svg'):
            target = payload / relative
        elif name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'CHANGELOG.md'):
            target = payload / relative
        else:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def compile_shell(payload):
    framework = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319'
    command = [str(framework / 'csc.exe'), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
               '/out:' + str(payload / 'ChatRescue.exe'), '/reference:System.Xaml.dll',
               '/reference:System.Web.Extensions.dll']
    command += ['/reference:System.Windows.Forms.dll']
    command += ['/reference:' + str(framework / 'WPF' / name) for name in
                ('PresentationFramework.dll', 'PresentationCore.dll', 'WindowsBase.dll')]
    command += [str(ROOT / 'desktop/windows' / name) for name in ('ChatRescue.cs', 'BackendTransport.cs', 'SourceImportFlow.cs')]
    subprocess.run(command, check=True)


def smoke_check(payload):
    environment = dict(os.environ, SQLCIPHER_LIBRARY=str(payload / 'runtime/e_sqlcipher.dll'), PYTHONUTF8='1')
    python = payload / 'runtime/python.exe'
    command = [str(python), '-B', '-m', 'chat_rescue', 'doctor']
    result = subprocess.run(command, cwd=payload, env=environment, capture_output=True, encoding='utf-8', check=True)
    report = json.loads(result.stdout)
    if not report['ready']:
        raise RuntimeError('Bundled runtime doctor failed')
    # Empty profile only; never borrow development settings or account keys.
    with tempfile.TemporaryDirectory(prefix='chat-rescue-package-profile-') as profile:
        result = subprocess.run([str(python), '-B', '-m', 'chat_rescue.windows_backend'], cwd=payload,
                                env=dict(environment, CHAT_RESCUE_WORKSPACE=profile), input='{"action":"status"}',
                                capture_output=True, encoding='utf-8', check=True)
        status = json.loads(result.stdout)
        if not status['ok'] or status['result']['settings']:
            raise RuntimeError('Bundled first-run settings are not empty')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iscc', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='new release output directory')
    parser.add_argument('--cache', type=Path, default=ROOT / 'build/windows-downloads')
    parser.add_argument('--python-archive', type=Path)
    parser.add_argument('--cipher-archive', type=Path)
    parser.add_argument('--allow-dirty', action='store_true', help='local packaging experiments only')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows x64 build host required')
    status = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)
    if status and not args.allow_dirty:
        parser.error('Commit public sources before building a release; --allow-dirty is for local experiments')
    version = re.search(r'__version__ = "([0-9.]+)"', (ROOT / 'chat_rescue/__init__.py').read_text())[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    args.cache.mkdir(parents=True, exist_ok=True)
    python_zip = archive(PYTHON_URL, PYTHON_SHA256, args.cache, args.python_archive)
    cipher_zip = archive(CIPHER_URL, CIPHER_SHA256, args.cache, args.cipher_archive)
    payload = output / 'portable'
    runtime = payload / 'runtime'; runtime.mkdir(parents=True)
    with zipfile.ZipFile(python_zip) as archive_file:
        archive_file.extractall(runtime)
    (runtime / 'python314._pth').write_text('python314.zip\n.\n../app\n', encoding='utf-8')
    with zipfile.ZipFile(cipher_zip) as archive_file:
        (runtime / 'e_sqlcipher.dll').write_bytes(archive_file.read('runtimes/win-x64/native/e_sqlcipher.dll'))
    copy_source(payload)
    shutil.copyfile(runtime / 'LICENSE.txt', payload / 'licenses/Python-LICENSE.txt')
    write_config(payload)
    (payload / 'FIRST-RUN.txt').write_text(
        'Chat Rescue ' + version + ' · Windows 实验版\n\n'
        '已内置 Python 和 SQLCipher，无需安装 Python、修改 PATH 或下载项目源码。\n'
        '需要 Windows 10（19041及以上）/ Windows 11 x64、系统 .NET Framework 4.8及以上。\n'
        '请自行安装腾讯原版微信并由本人登录；版本准入接受微信4.2.x.x，保留消息方向检查；实机证据仍仅有4.2.0.12。\n'
        '先登录 A（希望补回记录），读取核验并正常退出微信，再登录 B（仍保留记录），从手机 B 导入与 A 的聊天，电脑 B 已完整存在则直接核对，再勾选确认并读取 B。\n'
        '核对双方身份和具体恢复预览后才能应用。\n'
        '密钥、任务和原件保存在本人 LocalAppData/ChatRescue/Desktop 私有目录。\n'
        '卸载仅移除安装的程序文件，保留上述资料及微信记录。\n'
        '真实双账号GUI恢复、微信显示、手机迁移和空白库初始化仍待验收。\n'
        '使用说明见 docs/windows-desktop.md，第三方许可见 licenses/。\n', encoding='utf-8-sig')
    compile_shell(payload)
    report = smoke_check(payload)
    manifest = {'version': version, 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'source_dirty': bool(status), 'python': '3.14.10', 'sqlcipher': '4.5.2 community',
                'runtime_doctor': report, 'files': {p.relative_to(payload).as_posix(): sha256(p)
                for p in sorted(payload.rglob('*')) if p.is_file()}}
    (payload / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    portable_name = 'Chat-Rescue-' + version + '-Windows-x64-Portable'
    shutil.make_archive(str(output / portable_name), 'zip', payload)
    subprocess.run([str(args.iscc.resolve(strict=True)), '/DAppVersion=' + version, '/DPayloadDir=' + str(payload),
                    '/DReleaseDir=' + str(output), str(ROOT / 'desktop/windows/installer.iss')], check=True)
    assets = [output / (portable_name + '.zip'), output / ('Chat-Rescue-' + version + '-Windows-x64-Setup.exe')]
    (output / 'SHA256SUMS.txt').write_text(''.join(sha256(p) + '  ' + p.name + '\n' for p in assets), encoding='ascii')
    print(json.dumps({'version': version, 'output': str(output), 'assets': [str(p) for p in assets]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
